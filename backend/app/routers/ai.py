"""AI 配置、权限策略、连通性测试和只读草稿。"""

from __future__ import annotations

import secrets
import typing
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_service import (
    call_compatible_model,
    collect_sources,
    encrypt_api_key,
    is_private_endpoint,
    provider_output,
    test_provider,
    validate_provider_url,
)
from ..audit import emit_event, write_audit
from ..config import get_settings
from ..database import get_session
from ..enums import RecommendationStatus
from ..hardware_profile import collect_hardware_profile, run_light_benchmark
from ..local_ai import (
    complete_locally,
    embedding_runtime,
    llm_runtime,
    local_runtime_status,
)
from ..model_catalog import recommend_models
from ..model_packs import (
    activate_model_pack,
    cleanup_model_pack_uninstall_staging,
    deactivate_model_capability,
    finish_staged_model_pack_removal,
    install_model_pack,
    normalized_architecture,
    remove_installed_pack_files,
    rollback_staged_model_pack_removal,
    stage_model_pack_removal,
)
from ..models import (
    AIDraft,
    AIInvocation,
    AIModelActivation,
    AIModelPack,
    AIPolicy,
    AIProviderConfig,
    AIRecommendation,
    User,
    utcnow,
)
from ..needle_intent import needle_intent_runtime, preview_intent_with_needle
from ..problems import ProblemException
from ..recommendations import list_recommendations
from ..schemas import (
    AIDraftOut,
    AIModelPackOut,
    AIPolicyOut,
    AIPolicyPatch,
    AIProviderOut,
    AIProviderPatch,
    AIQueryRequest,
    AIRecommendationOut,
    HardwareBenchmarkOut,
    HardwareProfileOut,
    IntentPreviewOut,
    IntentPreviewRequest,
    LocalAIRuntimeOut,
    ModelRecommendationOut,
)
from ..security import get_current_user, require_admin

router = APIRouter(tags=["ai"])

MAX_MODEL_UPLOAD_BYTES = 4 * 1024**3


@router.get("/ai/hardware-profile", response_model=HardwareProfileOut)
def get_ai_hardware_profile(
    _admin: User = Depends(require_admin),
) -> HardwareProfileOut:
    return HardwareProfileOut.model_validate(collect_hardware_profile())


@router.post("/ai/hardware-profile/benchmark", response_model=HardwareBenchmarkOut)
def benchmark_ai_hardware(
    _admin: User = Depends(require_admin),
) -> HardwareBenchmarkOut:
    return HardwareBenchmarkOut.model_validate(run_light_benchmark())


@router.get("/ai/model-recommendations", response_model=typing.List[ModelRecommendationOut])
def get_model_recommendations(
    _admin: User = Depends(require_admin),
) -> list[ModelRecommendationOut]:
    profile = collect_hardware_profile()
    return [ModelRecommendationOut.model_validate(item) for item in recommend_models(profile)]


@router.post("/ai/intent/preview", response_model=IntentPreviewOut)
def get_intent_preview(
    payload: IntentPreviewRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> IntentPreviewOut:
    """只返回结构化预览，不执行任何模型提出的工具调用。"""

    return IntentPreviewOut.model_validate(preview_intent_with_needle(db, payload.text))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


@router.get("/admin/ai/model-packs", response_model=typing.List[AIModelPackOut])
def list_model_packs(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AIModelPackOut]:
    cleanup_model_pack_uninstall_staging()
    active_by_pack: dict[str, list[str]] = {}
    for activation in db.scalars(select(AIModelActivation)).all():
        active_by_pack.setdefault(activation.model_pack_id, []).append(activation.capability)
    return [
        AIModelPackOut.model_validate(pack).model_copy(
            update={"active_capabilities": sorted(active_by_pack.get(pack.id, []))}
        )
        for pack in db.scalars(select(AIModelPack).order_by(AIModelPack.created_at.desc())).all()
    ]


@router.post("/admin/ai/model-packs", response_model=AIModelPackOut, status_code=201)
async def upload_model_pack(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIModelPackOut:
    safe_name = PurePosixPath(file.filename or "partyops.partyops-modelpack").name
    if not safe_name.endswith(".partyops-modelpack"):
        raise ProblemException(422, "MODEL_PACK_EXTENSION_INVALID", "模型包格式不正确", "请选择 .partyops-modelpack 文件。")
    upload_dir = get_settings().models_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"upload-{secrets.token_hex(8)}.partyops-modelpack"
    size = 0
    pack: AIModelPack | None = None
    committed = False
    try:
        with path.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MODEL_UPLOAD_BYTES:
                    raise ProblemException(413, "MODEL_PACK_TOO_LARGE", "模型包超过4GB限制", "请重新获取精简模型包。")
                handle.write(chunk)
        pack = install_model_pack(path, safe_name, admin, db)
        write_audit(
            db,
            admin,
            "ai.model_pack_import",
            "ai_model_pack",
            pack.id,
            {"model_id": pack.model_id, "version": pack.version, "signature_valid": pack.signature_valid},
            client_ip(request),
        )
        db.commit()
        committed = True
        db.refresh(pack)
        return AIModelPackOut.model_validate(pack)
    except Exception:
        db.rollback()
        if pack is not None and not committed:
            remove_installed_pack_files(pack)
        path.unlink(missing_ok=True)
        raise


@router.post("/admin/ai/model-packs/{pack_id}/activate", response_model=AIModelPackOut)
def activate_local_model_pack(
    pack_id: str,
    request: Request,
    capability: str = Query(pattern=r"^(embedding|llm|intent_router)$"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIModelPackOut:
    pack = db.get(AIModelPack, pack_id)
    if not pack:
        raise ProblemException(404, "MODEL_PACK_NOT_FOUND", "模型包不存在", "请刷新模型包列表。")
    if pack.architecture not in {"universal", normalized_architecture()}:
        raise ProblemException(409, "MODEL_PACK_ARCH_MISMATCH", "模型包架构不匹配", f"本机需要 {normalized_architecture()} 模型包。")
    if capability == "llm":
        llm_runtime.stop()
    elif capability == "embedding":
        embedding_runtime.unload()
    elif capability == "intent_router":
        try:
            needle_intent_runtime.probe(pack)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProblemException(
                409,
                "NEEDLE_RUNTIME_INVALID",
                "Needle 意图运行时不可用",
                "原生库与当前平台不匹配、组件损坏或缺少必需接口。",
            ) from exc
    pack = activate_model_pack(db, pack, capability, admin.id)
    write_audit(db, admin, "ai.model_pack_activate", "ai_model_pack", pack.id, {"model_id": pack.model_id, "capability": capability}, client_ip(request))
    emit_event(db, "ai.model_pack_activated", pack.id, {"capability": capability})
    db.commit()
    db.refresh(pack)
    active = list(
        db.scalars(
            select(AIModelActivation.capability).where(AIModelActivation.model_pack_id == pack.id)
        ).all()
    )
    return AIModelPackOut.model_validate(pack).model_copy(update={"active_capabilities": sorted(active)})


@router.delete("/admin/ai/model-activations/{capability}", response_model=dict)
def deactivate_local_model_capability(
    capability: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    if capability not in {"embedding", "llm", "intent_router"}:
        raise ProblemException(422, "MODEL_CAPABILITY_INVALID", "模型能力无效", "请选择 embedding、llm 或 intent_router。")
    if capability == "llm":
        llm_runtime.stop()
    elif capability == "embedding":
        embedding_runtime.unload()
    elif capability == "intent_router":
        needle_intent_runtime.unload()
    pack = deactivate_model_capability(db, capability)
    write_audit(
        db,
        admin,
        "ai.model_capability_deactivate",
        "ai_model_pack",
        pack.id if pack else capability,
        {"capability": capability},
        client_ip(request),
    )
    db.commit()
    return {"capability": capability, "active": False, "pack_id": pack.id if pack else None}


@router.delete("/admin/ai/model-packs/{pack_id}", response_model=dict)
def uninstall_local_model_pack(
    pack_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """卸载未启用的签名包；业务数据与历史调用记录不受影响。"""

    pack = db.get(AIModelPack, pack_id)
    if not pack:
        raise ProblemException(404, "MODEL_PACK_NOT_FOUND", "模型包不存在", "请刷新模型包列表。")
    active = list(
        db.scalars(
            select(AIModelActivation.capability).where(
                AIModelActivation.model_pack_id == pack.id
            )
        ).all()
    )
    if active:
        raise ProblemException(
            409,
            "MODEL_PACK_ACTIVE",
            "模型包仍在使用",
            "请先停用该模型包的全部能力，再执行卸载。",
            extra={"active_capabilities": sorted(active)},
        )
    try:
        stage_root, moves = stage_model_pack_removal(pack)
    except OSError as exc:
        raise ProblemException(
            409,
            "MODEL_PACK_FILES_BUSY",
            "模型文件仍被系统占用",
            "请确认全部模型能力已停用，重新启动 PartyOps 后再试。",
        ) from exc
    try:
        write_audit(
            db,
            admin,
            "ai.model_pack_uninstall",
            "ai_model_pack",
            pack.id,
            {"model_id": pack.model_id, "version": pack.version},
            client_ip(request),
        )
        db.delete(pack)
        db.commit()
    except Exception:
        db.rollback()
        rollback_staged_model_pack_removal(stage_root, moves)
        raise
    cleanup_pending = finish_staged_model_pack_removal(stage_root)
    return {
        "id": pack_id,
        "uninstalled": True,
        "cleanup_pending": cleanup_pending,
    }


@router.get("/ai/runtime/status", response_model=LocalAIRuntimeOut)
def get_local_ai_status(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> LocalAIRuntimeOut:
    return LocalAIRuntimeOut.model_validate(local_runtime_status(db))


@router.get("/ai/recommendations", response_model=typing.List[AIRecommendationOut])
def get_ai_recommendations(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIRecommendation]:
    items = list_recommendations(db, user, limit)
    db.commit()
    return items


def _handle_recommendation(
    recommendation_id: str,
    target: RecommendationStatus,
    request: Request,
    if_match: str | None,
    user: User,
    db: Session,
) -> AIRecommendation:
    item = db.get(AIRecommendation, recommendation_id)
    if not item or item.user_id != user.id:
        raise ProblemException(404, "AI_RECOMMENDATION_NOT_FOUND", "智能建议不存在", "该建议可能已经过期。")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "智能建议已更新", "请刷新后重试。")
    if item.status != RecommendationStatus.PENDING:
        raise ProblemException(409, "AI_RECOMMENDATION_HANDLED", "智能建议已处理", "该建议不能重复操作。")
    item.status = target
    item.version += 1
    write_audit(db, user, f"ai.recommendation_{target.value}", "ai_recommendation", item.id, {"generator": item.generator.value}, client_ip(request))
    db.commit()
    db.refresh(item)
    return item


@router.post("/ai/recommendations/{recommendation_id}/accept", response_model=AIRecommendationOut)
def accept_ai_recommendation(
    recommendation_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIRecommendation:
    return _handle_recommendation(recommendation_id, RecommendationStatus.ACCEPTED, request, if_match, user, db)


@router.post("/ai/recommendations/{recommendation_id}/dismiss", response_model=AIRecommendationOut)
def dismiss_ai_recommendation(
    recommendation_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIRecommendation:
    return _handle_recommendation(recommendation_id, RecommendationStatus.DISMISSED, request, if_match, user, db)


@router.get("/ai/settings", response_model=AIProviderOut)
def get_ai_settings(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    return AIProviderOut.model_validate(provider_output(provider))


@router.patch("/ai/settings", response_model=AIProviderOut)
def patch_ai_settings(
    payload: AIProviderPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    if provider and provider.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 配置已更新", "请刷新后重试。")
    if payload.enabled and (not payload.base_url.strip() or not payload.model.strip()):
        raise ProblemException(422, "AI_CONFIG_INCOMPLETE", "AI 配置不完整", "启用前必须填写接口地址和模型名称。")
    if payload.base_url:
        validate_provider_url(
            payload.base_url.strip(),
            payload.trusted_intranet,
            resolve=False,
        )
    is_new = provider is None
    if is_new:
        provider = AIProviderConfig(created_by=admin.id, version=1)
        db.add(provider)
    provider.name = payload.name.strip()
    provider.base_url = payload.base_url.strip()
    provider.model = payload.model.strip()
    provider.enabled = payload.enabled
    provider.trusted_intranet = payload.trusted_intranet
    provider.timeout_seconds = payload.timeout_seconds
    if payload.api_key is not None:
        provider.api_key_encrypted = (
            encrypt_api_key(payload.api_key) if payload.api_key else ""
        )
    if not is_new:
        provider.version += 1
    db.flush()
    write_audit(
        db,
        admin,
        "ai.settings_update",
        "ai_provider",
        provider.id,
        {
            "enabled": provider.enabled,
            "model": provider.model,
            "private_endpoint": is_private_endpoint(provider.base_url),
            "has_api_key": bool(provider.api_key_encrypted),
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(provider)
    return AIProviderOut.model_validate(provider_output(provider))


@router.post("/ai/settings/test", response_model=AIProviderOut)
def test_ai_settings(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    if not provider or not provider.base_url:
        raise ProblemException(409, "AI_NOT_CONFIGURED", "尚未配置 AI", "请先保存模型服务地址。")
    try:
        test_provider(provider)
        provider.last_status = "connected"
        provider.last_error = ""
    except ProblemException as exc:
        provider.last_status = "failed"
        provider.last_error = exc.code
        provider.last_test_at = utcnow()
        db.commit()
        raise
    provider.last_test_at = utcnow()
    write_audit(db, admin, "ai.connection_test", "ai_provider", provider.id, {"status": provider.last_status}, client_ip(request))
    db.commit()
    return AIProviderOut.model_validate(provider_output(provider))


@router.get("/ai/policies", response_model=typing.List[AIPolicyOut])
def list_ai_policies(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AIPolicy]:
    return list(db.scalars(select(AIPolicy).order_by(AIPolicy.created_at)).all())


@router.post("/ai/policies", response_model=AIPolicyOut, status_code=201)
def create_ai_policy(
    payload: AIPolicyPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIPolicy:
    policy = AIPolicy(
        name=payload.name.strip(),
        allowed_root_ids=payload.allowed_root_ids,
        allowed_task_categories=payload.allowed_task_categories,
        allowed_file_types=payload.allowed_file_types,
        capabilities=[item.value for item in payload.capabilities],
        allow_restricted=False,
        active=payload.active,
        created_by=admin.id,
    )
    db.add(policy)
    db.flush()
    write_audit(
        db,
        admin,
        "ai.policy_create",
        "ai_policy",
        policy.id,
        {
            "roots": len(policy.allowed_root_ids),
            "capabilities": policy.capabilities,
            "allow_restricted": False,
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/ai/policies/{policy_id}", response_model=AIPolicyOut)
def patch_ai_policy(
    policy_id: str,
    payload: AIPolicyPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIPolicy:
    policy = db.get(AIPolicy, policy_id)
    if not policy:
        raise ProblemException(404, "AI_POLICY_NOT_FOUND", "AI 权限策略不存在", "未找到该策略。")
    if policy.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 权限策略已更新", "请刷新后重试。")
    policy.name = payload.name.strip()
    policy.allowed_root_ids = payload.allowed_root_ids
    policy.allowed_task_categories = payload.allowed_task_categories
    policy.allowed_file_types = payload.allowed_file_types
    policy.capabilities = [item.value for item in payload.capabilities]
    policy.allow_restricted = False
    policy.active = payload.active
    policy.version += 1
    write_audit(
        db,
        admin,
        "ai.policy_update",
        "ai_policy",
        policy.id,
        {
            "roots": len(policy.allowed_root_ids),
            "capabilities": policy.capabilities,
            "allow_restricted": False,
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.post("/ai/query", response_model=AIDraftOut, status_code=201)
def query_ai(
    payload: AIQueryRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    provider = db.scalar(
        select(AIProviderConfig).where(AIProviderConfig.enabled.is_(True))
    )
    policy = db.scalar(
        select(AIPolicy)
        .where(AIPolicy.active.is_(True))
        .order_by(AIPolicy.created_at)
    )
    if not policy or payload.capability.value not in policy.capabilities:
        raise ProblemException(403, "AI_CAPABILITY_DENIED", "AI 能力未授权", "请由管理员调整只读权限策略。")
    sources, excerpts = collect_sources(
        db,
        user,
        policy,
        payload.instruction,
        payload.task_ids,
        payload.file_ids,
        allow_sensitive_party_work=(
            user.role.value == "admin" and payload.confirm_sensitive
        ),
    )
    if not sources:
        raise ProblemException(422, "AI_SOURCE_EMPTY", "没有可用资料", "请先选择已授权事项或已索引文件。")
    private = bool(provider and is_private_endpoint(provider.base_url) and provider.trusted_intranet)
    if provider and not private and not payload.confirm_external:
        raise ProblemException(
            409,
            "AI_EXTERNAL_CONFIRM_REQUIRED",
            "需要确认资料外发",
            "当前接口不属于已信任内网。确认后只发送下列最小资料片段。",
            extra={"sources": sources, "source_count": len(sources)},
        )
    invocation = AIInvocation(
        user_id=user.id,
        provider_id=provider.id if provider else None,
        capability=payload.capability,
        source_count=len(sources),
        source_ids=[item["id"] for item in sources],
        status="running",
    )
    db.add(invocation)
    db.commit()
    try:
        if provider:
            content = call_compatible_model(
                provider,
                payload.capability,
                payload.instruction,
                excerpts,
            )
        else:
            content = complete_locally(db, payload.instruction, excerpts)
    except ProblemException as exc:
        invocation = db.get(AIInvocation, invocation.id)
        invocation.status = "failed"
        invocation.error_code = exc.code
        invocation.completed_at = utcnow()
        db.commit()
        raise
    draft = AIDraft(
        user_id=user.id,
        capability=payload.capability,
        title=f"AI 草稿 · {payload.capability.value}",
        content=content,
        sources=sources,
    )
    db.add(draft)
    invocation = db.get(AIInvocation, invocation.id)
    invocation.status = "completed"
    invocation.completed_at = utcnow()
    db.flush()
    write_audit(
        db,
        user,
        "ai.draft_create",
        "ai_draft",
        draft.id,
        {
            "capability": payload.capability.value,
            "source_count": len(sources),
            "provider_id": provider.id if provider else None,
            "generator": "external_llm" if provider else "local_llm",
        },
        client_ip(request),
    )
    emit_event(db, "ai.draft_created", draft.id, {"user_id": user.id})
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/ai/drafts", response_model=typing.List[AIDraftOut])
def list_ai_drafts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIDraft]:
    return list(
        db.scalars(
            select(AIDraft)
            .where(AIDraft.user_id == user.id)
            .order_by(AIDraft.created_at.desc())
            .limit(100)
        ).all()
    )


@router.post("/ai/drafts/{draft_id}/discard", response_model=AIDraftOut)
def discard_ai_draft(
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    draft = db.get(AIDraft, draft_id)
    if not draft or draft.user_id != user.id:
        raise ProblemException(404, "AI_DRAFT_NOT_FOUND", "AI 草稿不存在", "未找到该草稿。")
    if draft.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 草稿已更新", "请刷新后重试。")
    draft.status = "discarded"
    draft.version += 1
    write_audit(db, user, "ai.draft_discard", "ai_draft", draft.id, {}, client_ip(request))
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/ai/approvals", response_model=typing.List[AIDraftOut])
def list_ai_approvals(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIDraft]:
    statement = select(AIDraft).where(AIDraft.status == "draft")
    if user.role.value != "admin":
        statement = statement.where(AIDraft.user_id == user.id)
    return list(db.scalars(statement.order_by(AIDraft.created_at.desc())).all())


@router.post("/ai/drafts/{draft_id}/approve", response_model=AIDraftOut)
def approve_ai_draft(
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    draft = db.get(AIDraft, draft_id)
    if not draft or (draft.user_id != user.id and user.role.value != "admin"):
        raise ProblemException(404, "AI_DRAFT_NOT_FOUND", "AI 草稿不存在", "未找到可审批草稿。")
    if draft.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 草稿状态已变化", "请刷新后重试。")
    if draft.status != "draft":
        raise ProblemException(409, "AI_DRAFT_ALREADY_HANDLED", "AI 草稿已处理", "该草稿不能重复审批。")
    draft.status = "approved"
    draft.version += 1
    write_audit(db, user, "ai.draft_approve", "ai_draft", draft.id, {}, client_ip(request))
    db.commit()
    db.refresh(draft)
    return draft
