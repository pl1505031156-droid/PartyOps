"""规则推荐、语义候选和本地向量重排的边界分支回归。"""

from __future__ import annotations

from array import array
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import recommendations
from app.enums import RecommendationStatus, Sensitivity, TaskStatus
from app.problems import ProblemException


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class FakeDb:
    def __init__(self, scalar_rows=None, scalar_values=None) -> None:
        self.scalar_rows = list(scalar_rows or [])
        self.scalar_values = list(scalar_values or [])
        self.added = []
        self.flushed = 0

    def scalars(self, _query):
        return Rows(self.scalar_rows.pop(0) if self.scalar_rows else [])

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flushed += 1


def test_aware_upsert_and_rule_refresh_matrix(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    assert recommendations._aware(None) is None
    assert recommendations._aware(now) is now
    assert recommendations._aware(now.replace(tzinfo=None)).tzinfo is timezone.utc

    user = SimpleNamespace(id="user-1")
    task = SimpleNamespace(id="task-1", version=1, title="临期事项")
    existing = SimpleNamespace(id="existing")
    db = FakeDb(scalar_values=[existing])
    assert recommendations._upsert_rule(db, user, task, "due", "标题", "原因", 10, now) is existing

    stale_expired = SimpleNamespace(
        expires_at=now - timedelta(minutes=1), object_type="file", object_id="f1",
        object_version=1, status=RecommendationStatus.PENDING, version=1,
    )
    stale_changed = SimpleNamespace(
        expires_at=now + timedelta(days=1), object_type="task", object_id="changed",
        object_version=1, status=RecommendationStatus.PENDING, version=1,
    )
    tasks = [
        SimpleNamespace(id="restricted", version=1, title="敏感", sensitivity=Sensitivity.RESTRICTED, status=TaskStatus.IN_PROGRESS, internal_due_at=None, formal_due_at=None),
        SimpleNamespace(id="done", version=1, title="完成", sensitivity=Sensitivity.NORMAL, status=TaskStatus.COMPLETED, internal_due_at=None, formal_due_at=None),
        SimpleNamespace(id="soon", version=1, title="临期", sensitivity=Sensitivity.NORMAL, status=TaskStatus.WAITING_FEEDBACK, internal_due_at=now + timedelta(hours=4), formal_due_at=None),
        SimpleNamespace(id="review", version=1, title="审核", sensitivity=Sensitivity.NORMAL, status=TaskStatus.PENDING_REVIEW, internal_due_at=now - timedelta(days=1), formal_due_at=None),
        SimpleNamespace(id="normal", version=1, title="普通", sensitivity=Sensitivity.NORMAL, status=TaskStatus.IN_PROGRESS, internal_due_at=None, formal_due_at=None),
    ]
    monkeypatch.setattr(recommendations, "utcnow", lambda: now)
    monkeypatch.setattr(recommendations, "visible_tasks", lambda *_a: tasks)
    db = FakeDb(scalar_rows=[[stale_expired, stale_changed]])
    created = recommendations.refresh_rule_recommendations(db, user)
    assert created == 4
    assert stale_expired.status == RecommendationStatus.EXPIRED and stale_changed.status == RecommendationStatus.EXPIRED
    assert len(db.added) == 4 and db.flushed == 1


def test_index_candidates_cover_all_supported_object_types() -> None:
    task_full = SimpleNamespace(id="t1", version=1, title="事项", description="正文", category="党建", work_area="组织")
    task_empty = SimpleNamespace(id="t2", version=1, title="", description="", category="", work_area="")
    report_full = SimpleNamespace(id="r1", version=1, title="报告", summary="总结")
    report_empty = SimpleNamespace(id="r2", version=1, title="", summary="")
    archive_full = SimpleNamespace(id="a1", version=1, title="档案", document_no="1", source_unit="组织科", involved_persons=["张三"])
    archive_empty = SimpleNamespace(id="a2", version=1, title="", document_no="", source_unit="", involved_persons=[])
    knowledge_full = SimpleNamespace(id="k1", version=1, title="知识", category="制度")
    knowledge_empty = SimpleNamespace(id="k2", version=1, title="", category="")
    file_full = SimpleNamespace(id="f1", version=1, name="通知.pdf", relative_path="年度/通知.pdf")
    content_full = SimpleNamespace(id="c1", version=1, extracted_text="正文", ocr_text="OCR")
    content_empty = SimpleNamespace(id="c2", version=1, extracted_text="", ocr_text="")
    db = FakeDb(scalar_rows=[
        [task_full, task_empty],
        [report_full, report_empty],
        [archive_full, archive_empty],
        [knowledge_full, knowledge_empty],
        [file_full],
        ["重要", "年度"],
        [content_full, content_empty],
    ])
    values = recommendations._index_candidates(db, 60, "pack-1")
    types = {item[0] for item in values}
    assert types == {"task", "period_report", "archive_record", "knowledge", "workspace_file", "workspace_file_content"}
    assert "重要、年度" in next(item[3] for item in values if item[0] == "workspace_file")


def test_semantic_batch_readiness_checkpoint_and_failure_matrix(monkeypatch) -> None:
    db = FakeDb()
    monkeypatch.setattr(recommendations, "local_ai_readiness", lambda *_a, **_k: {"ready": False})
    assert recommendations.index_semantic_batch(db) == 0
    monkeypatch.setattr(recommendations, "local_ai_readiness", lambda *_a, **_k: {"ready": True})
    monkeypatch.setattr(recommendations, "active_model_pack", lambda *_a: None)
    assert recommendations.index_semantic_batch(db) == 0

    pack = SimpleNamespace(id="pack-1")
    monkeypatch.setattr(recommendations, "active_model_pack", lambda *_a: pack)
    monkeypatch.setattr(recommendations, "_index_candidates", lambda *_a: [])
    assert recommendations.index_semantic_batch(db) == 0

    now = datetime.now(timezone.utc)
    existing_blob = SimpleNamespace(object_version=1, content_sha256="same", embedding_blob=b"blob", indexed_at=now)
    recent_failure = SimpleNamespace(object_version=1, content_sha256="same2", embedding_blob=None, indexed_at=now)
    db = FakeDb(scalar_values=[existing_blob, recent_failure])
    monkeypatch.setattr(recommendations, "_index_candidates", lambda *_a: [
        ("task", "t1", 1, "same text"),
        ("task", "t2", 1, "recent failure"),
    ])
    monkeypatch.setattr(recommendations.hashlib, "sha256", lambda value: SimpleNamespace(hexdigest=lambda: "same" if b"same text" == value else "same2"))
    assert recommendations.index_semantic_batch(db, limit=2) == 0


def test_semantic_rerank_all_guards_and_sorting(monkeypatch) -> None:
    db = FakeDb()
    items = [{"type": "task", "id": "t1"}, {"type": "file", "id": "f1"}, {"type": "other", "id": "x"}]
    assert recommendations.semantic_rerank_search_items(db, "", items) is items
    assert recommendations.semantic_rerank_search_items(db, "查询", items[:1]) is not None

    pack = SimpleNamespace(id="pack-1")
    monkeypatch.setattr(recommendations, "local_ai_readiness", lambda *_a, **_k: {"ready": False})
    monkeypatch.setattr(recommendations, "active_model_pack", lambda *_a: pack)
    monkeypatch.setattr(recommendations.embedding_runtime, "loaded_for", lambda _id: True)
    assert recommendations.semantic_rerank_search_items(db, "查询", items) is items
    monkeypatch.setattr(recommendations, "local_ai_readiness", lambda *_a, **_k: {"ready": True})
    monkeypatch.setattr(recommendations, "active_model_pack", lambda *_a: None)
    assert recommendations.semantic_rerank_search_items(db, "查询", items) is items
    monkeypatch.setattr(recommendations, "active_model_pack", lambda *_a: pack)
    monkeypatch.setattr(recommendations.embedding_runtime, "loaded_for", lambda _id: False)
    assert recommendations.semantic_rerank_search_items(db, "查询", items) is items
    monkeypatch.setattr(recommendations.embedding_runtime, "loaded_for", lambda _id: True)
    assert recommendations.semantic_rerank_search_items(db, "查询", [{"type": "other", "id": "x"}, {"type": "other", "id": "y"}]) is not None

    monkeypatch.setattr(recommendations.embedding_runtime, "encode", lambda *_a, **_k: (_ for _ in ()).throw(ProblemException(400, "X", "x", "x")))
    assert recommendations.semantic_rerank_search_items(db, "查询", items) is items

    query = array("f", [1.0, 0.0]).tobytes()
    monkeypatch.setattr(recommendations.embedding_runtime, "encode", lambda *_a, **_k: [query])
    checkpoints = [
        SimpleNamespace(object_type="other", object_id="x", embedding_blob=query),
        SimpleNamespace(object_type="task", object_id="t1", embedding_blob=b""),
        SimpleNamespace(object_type="task", object_id="t1", embedding_blob=array("f", [1.0]).tobytes()),
        SimpleNamespace(object_type="task", object_id="t1", embedding_blob=array("f", [0.2, 0.0]).tobytes()),
        SimpleNamespace(object_type="workspace_file", object_id="f1", embedding_blob=array("f", [0.9, 0.0]).tobytes()),
    ]
    db = FakeDb(scalar_rows=[checkpoints])
    reranked = recommendations.semantic_rerank_search_items(db, "查询", items)
    assert [item["id"] for item in reranked[:2]] == ["f1", "t1"]
