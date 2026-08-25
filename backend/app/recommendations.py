"""可解释推荐与轻量语义索引。

规则层始终可用；语义层只处理非敏感业务元数据，且只有主机空闲、
模型包有效时才运行。共享文件正文还必须由目录发布人或管理员逐目录开启。
"""

from __future__ import annotations

import hashlib
import logging
from array import array
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from .compat import strict_zip
from .enums import (
    RecommendationGenerator,
    RecommendationStatus,
    Sensitivity,
    TaskStatus,
)
from .local_ai import embedding_runtime, local_ai_readiness
from .model_packs import active_model_pack
from .models import (
    AIRecommendation,
    ArchiveRecord,
    KnowledgeEntry,
    PeriodReport,
    SemanticIndexCheckpoint,
    Task,
    User,
    WorkspaceFile,
    WorkspaceFileTag,
    WorkspaceRoot,
    utcnow,
)
from .problems import ProblemException
from .task_service import visible_tasks

TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.ARCHIVED}
SEMANTIC_FAILURE_RETRY = timedelta(hours=6)
logger = logging.getLogger("partyops.recommendations")


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _upsert_rule(
    db: Session,
    user: User,
    task: Task,
    reason_code: str,
    title: str,
    reason: str,
    score: int,
    expires_at: datetime,
) -> AIRecommendation:
    dedupe = f"rules:task:{task.id}:{task.version}:{reason_code}:{user.id}"
    item = db.scalar(select(AIRecommendation).where(AIRecommendation.dedupe_key == dedupe))
    if item:
        return item
    item = AIRecommendation(
        user_id=user.id,
        generator=RecommendationGenerator.RULES,
        status=RecommendationStatus.PENDING,
        title=title,
        reason=reason,
        content="这是排序和风险规则生成的建议，不会自动修改事项。",
        score=score,
        object_type="task",
        object_id=task.id,
        object_version=task.version,
        route=f"/tasks/{task.id}",
        sources=[{"type": "task", "id": task.id, "title": task.title}],
        dedupe_key=dedupe,
        expires_at=expires_at,
    )
    db.add(item)
    return item


def refresh_rule_recommendations(db: Session, user: User) -> int:
    """刷新当前用户的规则建议；受限事项在入口处即被排除。"""

    now = utcnow()
    active_task_versions = {task.id: task.version for task in visible_tasks(db, user)}
    stale = db.scalars(
        select(AIRecommendation).where(
            AIRecommendation.user_id == user.id,
            AIRecommendation.status == RecommendationStatus.PENDING,
        )
    ).all()
    for item in stale:
        if (_aware(item.expires_at) or now) <= now or (
            item.object_type == "task"
            and active_task_versions.get(item.object_id) != item.object_version
        ):
            item.status = RecommendationStatus.EXPIRED
            item.version += 1

    created = 0
    for task in visible_tasks(db, user):
        if task.sensitivity == Sensitivity.RESTRICTED or task.status in TERMINAL_STATUSES:
            continue
        due = _aware(task.internal_due_at or task.formal_due_at)
        if due and due < now:
            _upsert_rule(
                db,
                user,
                task,
                "overdue",
                f"优先处理：{task.title}",
                f"该事项已逾期{max(1, (now - due).days)}天，建议先确认阻塞原因和下一步。",
                100,
                now + timedelta(days=2),
            )
            created += 1
        elif due and due <= now + timedelta(days=3):
            hours = max(1, int((due - now).total_seconds() // 3600))
            _upsert_rule(
                db,
                user,
                task,
                "due_soon",
                f"临近截止：{task.title}",
                f"距离内部或正式截止时间约{hours}小时，建议检查材料和责任分工。",
                85,
                due + timedelta(hours=12),
            )
            created += 1
        if task.status == TaskStatus.PENDING_REVIEW:
            _upsert_rule(
                db,
                user,
                task,
                "pending_review",
                f"待审核：{task.title}",
                "事项已提交审核，建议核对最终材料和完成说明。",
                80,
                now + timedelta(days=3),
            )
            created += 1
        elif task.status == TaskStatus.WAITING_FEEDBACK:
            _upsert_rule(
                db,
                user,
                task,
                "waiting_feedback",
                f"等待反馈：{task.title}",
                "事项处于等待反馈状态，建议确认对方反馈节点并保留沟通记录。",
                70,
                now + timedelta(days=3),
            )
            created += 1
    db.flush()
    return created


def list_recommendations(db: Session, user: User, limit: int = 50) -> list[AIRecommendation]:
    refresh_rule_recommendations(db, user)
    now = utcnow()
    return list(
        db.scalars(
            select(AIRecommendation)
            .where(
                AIRecommendation.user_id == user.id,
                AIRecommendation.status == RecommendationStatus.PENDING,
                AIRecommendation.expires_at > now,
            )
            .order_by(AIRecommendation.score.desc(), AIRecommendation.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
    )


def _pending_semantic_object(
    object_type: str,
    object_id_column,
    object_version_column,
    model_pack_id: str,
    retry_before: datetime,
):
    """只选择未处理、已变化或隔离冷却到期的语义对象。"""

    processed = exists(
        select(SemanticIndexCheckpoint.id).where(
            SemanticIndexCheckpoint.object_type == object_type,
            SemanticIndexCheckpoint.object_id == object_id_column,
            SemanticIndexCheckpoint.object_version == object_version_column,
            SemanticIndexCheckpoint.model_pack_id == model_pack_id,
            or_(
                SemanticIndexCheckpoint.embedding_blob.is_not(None),
                SemanticIndexCheckpoint.indexed_at >= retry_before,
            ),
        )
    )
    return ~processed


def _index_candidates(
    db: Session,
    limit: int,
    model_pack_id: str,
) -> list[tuple[str, str, int, str]]:
    candidates: list[tuple[str, str, int, str]] = []
    per_type = max(1, limit // 6)
    retry_before = utcnow() - SEMANTIC_FAILURE_RETRY
    tasks = db.scalars(
        select(Task)
        .where(
            Task.deleted_at.is_(None),
            Task.sensitivity == Sensitivity.NORMAL,
            _pending_semantic_object(
                "task", Task.id, Task.version, model_pack_id, retry_before
            ),
        )
        .order_by(Task.updated_at.asc())
        .limit(per_type)
    ).all()
    for task in tasks:
        text = "\n".join(part for part in [task.title, task.description, task.category, task.work_area] if part).strip()
        if text:
            candidates.append(("task", task.id, task.version, text))
    remaining = max(0, limit - len(candidates))
    if remaining:
        reports = db.scalars(
            select(PeriodReport)
            .where(
                _pending_semantic_object(
                    "period_report",
                    PeriodReport.id,
                    PeriodReport.version,
                    model_pack_id,
                    retry_before,
                )
            )
            .order_by(PeriodReport.updated_at.asc())
            .limit(min(remaining, per_type))
        ).all()
        for report in reports:
            text = "\n".join(part for part in [report.title, report.summary] if part).strip()
            if text:
                candidates.append(("period_report", report.id, report.version, text))
    remaining = max(0, limit - len(candidates))
    if remaining:
        archives = db.scalars(
            select(ArchiveRecord)
            .where(
                _pending_semantic_object(
                    "archive_record",
                    ArchiveRecord.id,
                    ArchiveRecord.version,
                    model_pack_id,
                    retry_before,
                )
            )
            .order_by(ArchiveRecord.updated_at.asc())
            .limit(min(remaining, per_type))
        ).all()
        for record in archives:
            text = "\n".join(
                part
                for part in [
                    record.title,
                    record.document_no,
                    record.source_unit,
                    "、".join(record.involved_persons or []),
                ]
                if part
            ).strip()
            if text:
                candidates.append(("archive_record", record.id, record.version, text))
    remaining = max(0, limit - len(candidates))
    if remaining:
        entries = db.scalars(
            select(KnowledgeEntry)
            .where(
                _pending_semantic_object(
                    "knowledge",
                    KnowledgeEntry.id,
                    KnowledgeEntry.version,
                    model_pack_id,
                    retry_before,
                )
            )
            .order_by(KnowledgeEntry.updated_at.asc())
            .limit(min(remaining, per_type))
        ).all()
        for entry in entries:
            text = "\n".join(part for part in [entry.title, entry.category] if part).strip()
            if text:
                candidates.append(("knowledge", entry.id, entry.version, text))
    remaining = max(0, limit - len(candidates))
    if remaining:
        files = db.scalars(
            select(WorkspaceFile)
            .join(WorkspaceRoot, WorkspaceRoot.id == WorkspaceFile.root_id)
            .where(
                WorkspaceRoot.enabled.is_(True),
                WorkspaceFile.in_scope.is_(True),
                WorkspaceFile.is_directory.is_(False),
                WorkspaceFile.status != "missing",
                _pending_semantic_object(
                    "workspace_file",
                    WorkspaceFile.id,
                    WorkspaceFile.version,
                    model_pack_id,
                    retry_before,
                ),
            )
            .order_by(WorkspaceFile.indexed_at.asc())
            .limit(min(remaining, per_type))
        ).all()
        for item in files:
            tags = list(
                db.scalars(
                    select(WorkspaceFileTag.tag).where(WorkspaceFileTag.file_id == item.id)
                ).all()
            )
            metadata = "\n".join([item.name, item.relative_path, "、".join(tags)]).strip()
            if metadata:
                candidates.append(("workspace_file", item.id, item.version, metadata))
    remaining = max(0, limit - len(candidates))
    if remaining:
        content_files = db.scalars(
            select(WorkspaceFile)
            .join(WorkspaceRoot, WorkspaceRoot.id == WorkspaceFile.root_id)
            .where(
                WorkspaceRoot.enabled.is_(True),
                WorkspaceRoot.semantic_content_enabled.is_(True),
                WorkspaceFile.in_scope.is_(True),
                WorkspaceFile.is_directory.is_(False),
                WorkspaceFile.status != "missing",
                or_(
                    WorkspaceFile.extracted_text != "",
                    WorkspaceFile.ocr_text != "",
                ),
                _pending_semantic_object(
                    "workspace_file_content",
                    WorkspaceFile.id,
                    WorkspaceFile.version,
                    model_pack_id,
                    retry_before,
                ),
            )
            .order_by(WorkspaceFile.indexed_at.asc())
            .limit(remaining)
        ).all()
        for item in content_files:
            content = "\n".join(part for part in [item.extracted_text, item.ocr_text] if part).strip()
            if content:
                candidates.append(("workspace_file_content", item.id, item.version, content))
    return candidates


def index_semantic_batch(db: Session, limit: int = 8) -> int:
    """在主机空闲时增量建立语义检查点；失败由调度器隔离。"""

    readiness = local_ai_readiness(db, capability="embedding")
    if not readiness["ready"]:
        return 0
    pack = active_model_pack(db, "embedding")
    if not pack:
        return 0
    pending: list[tuple[str, str, int, str, str]] = []
    retry_before = utcnow() - SEMANTIC_FAILURE_RETRY
    for object_type, object_id, version, text in _index_candidates(
        db, limit * 4, pack.id
    ):
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        checkpoint = db.scalar(
            select(SemanticIndexCheckpoint).where(
                SemanticIndexCheckpoint.object_type == object_type,
                SemanticIndexCheckpoint.object_id == object_id,
                SemanticIndexCheckpoint.model_pack_id == pack.id,
            )
        )
        if (
            checkpoint
            and checkpoint.object_version == version
            and checkpoint.content_sha256 == content_hash
        ):
            if checkpoint.embedding_blob is not None:
                continue
            indexed_at = _aware(checkpoint.indexed_at)
            if indexed_at and indexed_at >= retry_before:
                continue
        pending.append((object_type, object_id, version, text, content_hash))
        if len(pending) >= limit:
            break
    if not pending:
        return 0
    successful: list[tuple[tuple[str, str, int, str, str], bytes]] = []
    failed: list[tuple[str, str, int, str, str]] = []

    def encode_group(group: list[tuple[str, str, int, str, str]]) -> None:
        try:
            vectors = embedding_runtime.encode(pack, [item[3] for item in group])
            successful.extend(strict_zip(group, vectors))
        except Exception as exc:  # 单条坏数据不能放大为整个调度批次故障
            if len(group) > 1:
                middle = len(group) // 2
                encode_group(group[:middle])
                encode_group(group[middle:])
                return
            failed.append(group[0])
            logger.warning(
                "semantic_index_item_quarantined type=%s id=%s error=%s",
                group[0][0],
                group[0][1],
                type(exc).__name__,
            )

    encode_group(pending)
    now = utcnow()
    for item, vector in successful:
        object_type, object_id, version, _text, content_hash = item
        checkpoint = db.scalar(
            select(SemanticIndexCheckpoint).where(
                SemanticIndexCheckpoint.object_type == object_type,
                SemanticIndexCheckpoint.object_id == object_id,
                SemanticIndexCheckpoint.model_pack_id == pack.id,
            )
        )
        if not checkpoint:
            checkpoint = SemanticIndexCheckpoint(
                object_type=object_type,
                object_id=object_id,
                model_pack_id=pack.id,
            )
            db.add(checkpoint)
        checkpoint.object_version = version
        checkpoint.embedding_blob = vector
        checkpoint.content_sha256 = content_hash
        checkpoint.indexed_at = now
    for object_type, object_id, version, _text, content_hash in failed:
        checkpoint = db.scalar(
            select(SemanticIndexCheckpoint).where(
                SemanticIndexCheckpoint.object_type == object_type,
                SemanticIndexCheckpoint.object_id == object_id,
                SemanticIndexCheckpoint.model_pack_id == pack.id,
            )
        )
        if not checkpoint:
            checkpoint = SemanticIndexCheckpoint(
                object_type=object_type,
                object_id=object_id,
                model_pack_id=pack.id,
            )
            db.add(checkpoint)
        checkpoint.object_version = version
        checkpoint.embedding_blob = None
        checkpoint.content_sha256 = content_hash
        checkpoint.indexed_at = now
    db.flush()
    return len(successful)


def semantic_rerank_search_items(
    db: Session,
    keyword: str,
    items: list[dict[str, object]],
) -> list[dict[str, object]]:
    """对 FTS/字段检索已命中的最小结果做可选语义重排。

    为保护普通请求时延，本函数只复用后台已经加载的语义模型；不会在用户
    搜索时冷启动 ONNX，也不会扩展原检索结果或读取原始文件正文。
    """

    query = keyword.strip()
    if not query or len(items) < 2:
        return items
    readiness = local_ai_readiness(db, capability="embedding")
    pack = active_model_pack(db, "embedding")
    if not readiness["ready"] or not pack or not embedding_runtime.loaded_for(pack.id):
        return items
    type_map = {
        "task": "task",
        "report": "period_report",
        "file": "workspace_file",
        "archive": "archive_record",
        "knowledge": "knowledge",
    }
    object_keys = [
        (type_map[str(item.get("type"))], str(item.get("id", "")))
        for item in items
        if str(item.get("type")) in type_map
    ]
    if not object_keys:
        return items
    try:
        query_blob = embedding_runtime.encode(pack, [query], is_query=True)[0]
    except (ProblemException, IndexError):
        return items
    query_vector = array("f")
    query_vector.frombytes(query_blob)
    checkpoints = db.scalars(
        select(SemanticIndexCheckpoint).where(
            SemanticIndexCheckpoint.model_pack_id == pack.id,
            SemanticIndexCheckpoint.embedding_blob.is_not(None),
        )
    ).all()
    allowed = set(object_keys)
    scores: dict[tuple[str, str], float] = {}
    for checkpoint in checkpoints:
        key = (checkpoint.object_type, checkpoint.object_id)
        if key not in allowed or not checkpoint.embedding_blob:
            continue
        vector = array("f")
        vector.frombytes(checkpoint.embedding_blob)
        if len(vector) != len(query_vector):
            continue
        scores[key] = sum(
            left * right
            for left, right in strict_zip(query_vector, vector)
        )
    if not scores:
        return items
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            -scores.get(
                (
                    type_map.get(str(pair[1].get("type", "")), ""),
                    str(pair[1].get("id", "")),
                ),
                -2.0,
            ),
            pair[0],
        )
    )
    return [item for _index, item in indexed]
