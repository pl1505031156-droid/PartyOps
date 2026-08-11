"""统一检索与工作台跨业务类型、权限过滤和空值分支回归。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.models import ArchiveCategory, Task, WorkspaceRoot
from app.problems import ProblemException
from app.routers import productivity


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, rows, objects=None) -> None:
        self.rows = list(rows)
        self.objects = objects or {}

    def scalars(self, _query):
        return Rows(self.rows.pop(0) if self.rows else [])

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))


def value(text: str):
    return SimpleNamespace(value=text)


def test_parse_transition_and_workbench_empty_value_branches(monkeypatch) -> None:
    assert productivity.client_ip(SimpleNamespace(client=None)) == ""
    with pytest.raises(ProblemException):
        productivity.parse_version(None)
    with pytest.raises(ProblemException):
        productivity.parse_version("bad")
    assert productivity.parse_version('"12"') == 12
    monkeypatch.setattr(productivity, "transition", lambda *_a: (_ for _ in ()).throw(ProblemException(409, "X", "x", "x")))
    assert not productivity._transition_matches(value("in_progress"), "bad", value("completed"))

    now = datetime.now(timezone.utc)
    transfers = [
        SimpleNamespace(id="t1", original_name="一.txt", status=value("failed"), direction=value("host_to_device"), completed_chunks=0, total_chunks=0),
        SimpleNamespace(id="t2", original_name="二.txt", status=value("transferring"), direction=value("device_to_host"), completed_chunks=1, total_chunks=2),
    ]
    devices = [
        SimpleNamespace(id="d1", name="一号", status=value("online"), last_seen_at=None),
        SimpleNamespace(id="d2", name="二号", status=value("offline"), last_seen_at=now),
    ]
    files = [SimpleNamespace(id="f1", name="材料", relative_path="材料", status=value("indexed"), availability=value("online"))]
    db = Db([transfers, devices, files])
    monkeypatch.setattr("app.task_service.dashboard", lambda *_a: SimpleNamespace(model_dump=lambda **_k: {"ok": True}))
    result = productivity.workbench(SimpleNamespace(id="user"), db)
    assert [item["progress"] for item in result["pending_transfers"]] == [0, 50]
    assert result["devices"][0]["last_seen_at"] is None and result["devices"][1]["last_seen_at"]


@pytest.mark.parametrize("fts_ids", [[], ["archive-good"]])
def test_global_search_all_business_types_and_permission_filters(monkeypatch, fts_ids) -> None:
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(id="task-1", title="党建检索", category="", status=value("in_progress"), updated_at=now)
    files = [
        SimpleNamespace(id="dir", root_id="root-good", is_directory=True),
        SimpleNamespace(id="file-missing-root", root_id="missing", is_directory=False),
        SimpleNamespace(id="file-denied", root_id="root-denied", is_directory=False),
        SimpleNamespace(id="file-good", root_id="root-good", is_directory=False, name="党建文件", extension="", availability=value("online"), modified_at=None, last_seen_at=None),
        SimpleNamespace(id="file-time", root_id="root-good", is_directory=False, name="党建时间文件", extension="pdf", availability=value("online"), modified_at=now, last_seen_at=None),
    ]
    records = [
        SimpleNamespace(id="archive-no-category", category_id="missing", status=value("active")),
        SimpleNamespace(id="archive-inactive", category_id="inactive", status=value("active")),
        SimpleNamespace(id="archive-denied", category_id="denied", status=value("active")),
        SimpleNamespace(id="archive-void", category_id="good", status=value("voided")),
        SimpleNamespace(id="archive-good", category_id="good", status=value("active"), title="党建档案", archive_year=2026, document_no="", updated_at=now),
    ]
    contacts = [
        SimpleNamespace(id="c0", name="无关", organization="其他", note="",),
        SimpleNamespace(id="c1", name="党建联系人", organization="", note="",),
    ]
    journals = [
        SimpleNamespace(id="j0", task_id="missing-task", title="党建日志", content="", immutable=False, updated_at=now),
        SimpleNamespace(id="j1", task_id="denied-task", title="党建日志", content="", immutable=False, updated_at=now),
        SimpleNamespace(id="j2", task_id=None, title="无关日志", content="", immutable=False, updated_at=now),
        SimpleNamespace(id="j3", task_id=None, title="党建系统日志", content="", immutable=True, updated_at=now),
    ]
    reports = [
        SimpleNamespace(id="r0", title="无关", summary="", period_key="2026", status=value("draft"), updated_at=now),
        SimpleNamespace(id="r1", title="党建报告", summary="", period_key="2026", status=value("published"), updated_at=now),
    ]
    knowledge = [
        SimpleNamespace(id="k0", title="无关", category="", body="", updated_at=now),
        SimpleNamespace(id="k1", title="党建知识", category="", body="", updated_at=now),
    ]
    devices = [
        SimpleNamespace(id="d0", name="无关设备", platform="windows", architecture="amd64", local_username="u", status=value("online"), last_seen_at=None),
        SimpleNamespace(id="d1", name="党建协同机", platform="windows", architecture="amd64", local_username="u", status=value("online"), last_seen_at=now),
    ]
    root_good = SimpleNamespace(id="root-good")
    root_denied = SimpleNamespace(id="root-denied")
    category_inactive = SimpleNamespace(id="inactive", active=False, name="停用")
    category_denied = SimpleNamespace(id="denied", active=True, name="拒绝")
    category_good = SimpleNamespace(id="good", active=True, name="年度档案")
    objects = {
        (WorkspaceRoot, "root-good"): root_good,
        (WorkspaceRoot, "root-denied"): root_denied,
        (ArchiveCategory, "inactive"): category_inactive,
        (ArchiveCategory, "denied"): category_denied,
        (ArchiveCategory, "good"): category_good,
        (Task, "denied-task"): SimpleNamespace(id="denied-task"),
    }
    db = Db([[task], records, contacts, journals, reports, knowledge, devices], objects)
    user = SimpleNamespace(id="admin", role=value("admin"))
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), cookies={})
    monkeypatch.setattr(productivity, "search_workspace_files", lambda *_a, **_k: files)
    monkeypatch.setattr(productivity, "request_device", lambda *_a: SimpleNamespace(id="device-1"))
    monkeypatch.setattr(productivity, "workspace_root_permissions", lambda _db, root, *_a, **_k: {"browse": root is root_good})
    monkeypatch.setattr(productivity, "archive_fts_search", lambda *_a, **_k: fts_ids)
    monkeypatch.setattr(productivity, "can_view_category", lambda _db, category, _user: category is not category_denied)
    monkeypatch.setattr(productivity, "can_view_task", lambda *_a: False)
    monkeypatch.setattr(productivity, "semantic_rerank_search_items", lambda _db, _keyword, items: list(reversed(items)))
    result = productivity.global_search(request, "党建", 100, user, db)
    types = {item["type"] for item in result["items"]}
    assert {"task", "file", "archive", "contact", "journal", "report", "knowledge", "device"} <= types
    assert result["items"][0]["type"] == "device"


def test_global_search_empty_query_hides_file_and_archive_blocks(monkeypatch) -> None:
    db = Db([[], [], [], [], [], []])
    user = SimpleNamespace(id="staff", role=value("staff"))
    monkeypatch.setattr(productivity, "search_workspace_files", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("空查询不得扫描文件")))
    monkeypatch.setattr(productivity, "archive_fts_search", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("空查询不得扫描档案")))
    monkeypatch.setattr(productivity, "semantic_rerank_search_items", lambda _db, _keyword, items: items)
    result = productivity.global_search(SimpleNamespace(client=None, cookies={}), "   ", 10, user, db)
    assert result == {"query": "", "items": [], "total": 0}
