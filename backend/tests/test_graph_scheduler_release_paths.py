"""对象关联权限与后台调度的正式发布回归。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import object_graph, scheduler
from app.enums import LinkType, ObjectType, UserRole, WorkspaceRootSource
from app.models import (
    ArchiveCategory,
    ArchiveRecord,
    Contact,
    KnowledgeEntry,
    Notification,
    PeriodReport,
    Task,
    TopicSpace,
    WorkJournalEntry,
    WorkspaceFile,
    WorkspaceRoot,
)
from app.problems import ProblemException


class _ScalarRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _ObjectDb:
    def __init__(self, objects: dict[tuple[type, str], object]):
        self.objects = objects
        self.links: list[object] = []

    def get(self, model, object_id):
        return self.objects.get((model, object_id))

    def scalars(self, _statement):
        return _ScalarRows(self.links)


def test_describe_object_enforces_every_object_boundary(monkeypatch) -> None:
    """跨域关联只能返回当前用户确实可见的对象，不泄露标题。"""

    staff = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    admin = SimpleNamespace(id="admin-1", role=UserRole.ADMIN)
    root = SimpleNamespace(
        id="root-1",
        enabled=True,
        approval_status="approved",
        source=WorkspaceRootSource.DEVICE,
        device_id="device-1",
    )
    objects = {
        (Task, "task-1"): SimpleNamespace(id="task-1", title="协同任务"),
        (WorkspaceRoot, "root-1"): root,
        (WorkspaceFile, "file-1"): SimpleNamespace(
            id="file-1", root_id="root-1", name="共享材料.pdf", in_scope=True
        ),
        (ArchiveRecord, "archive-1"): SimpleNamespace(
            id="archive-1", category_id="category-1", title="重要档案"
        ),
        (ArchiveCategory, "category-1"): SimpleNamespace(id="category-1"),
        (WorkJournalEntry, "journal-own"): SimpleNamespace(
            id="journal-own", title="我的日志", created_by="user-1", task_id=None
        ),
        (WorkJournalEntry, "journal-task"): SimpleNamespace(
            id="journal-task", title="任务日志", created_by="other", task_id="task-1"
        ),
        (PeriodReport, "report-1"): SimpleNamespace(id="report-1", title="月报"),
        (KnowledgeEntry, "knowledge-1"): SimpleNamespace(
            id="knowledge-1", title="制度知识"
        ),
        (Contact, "contact-1"): SimpleNamespace(id="contact-1", name="联络人"),
        (TopicSpace, "topic-1"): SimpleNamespace(
            id="topic-1", name="专题空间", owner_id="user-1"
        ),
    }
    db = _ObjectDb(objects)
    monkeypatch.setattr(object_graph, "can_view_task", lambda *_args: True)
    monkeypatch.setattr(object_graph, "can_view_category", lambda *_args: True)
    monkeypatch.setattr(
        object_graph,
        "grant_allows",
        lambda _db, _user, _device, _root, capability: capability == "share",
    )

    expected = {
        (ObjectType.TASK, "task-1"): ("协同任务", "/tasks/task-1"),
        (ObjectType.WORKSPACE_FILE, "file-1"): ("共享材料.pdf", "/workspace?file=file-1"),
        (ObjectType.ARCHIVE_RECORD, "archive-1"): ("重要档案", "/archives?record=archive-1"),
        (ObjectType.JOURNAL, "journal-own"): ("我的日志", "/journal?entry=journal-own"),
        (ObjectType.JOURNAL, "journal-task"): ("任务日志", "/journal?entry=journal-task"),
        (ObjectType.PERIOD_REPORT, "report-1"): ("月报", "/reports?report=report-1"),
        (ObjectType.KNOWLEDGE, "knowledge-1"): ("制度知识", "/knowledge?entry=knowledge-1"),
        (ObjectType.CONTACT, "contact-1"): ("联络人", "/knowledge?contact=contact-1"),
        (ObjectType.TOPIC, "topic-1"): ("专题空间", "/topics?topic=topic-1"),
    }
    for (kind, object_id), value in expected.items():
        descriptor = object_graph.describe_object(db, kind, object_id, staff)
        assert (descriptor.title, descriptor.route) == value

    # 管理员可访问已批准根；越界、停用与未授权均按不存在处理，避免枚举对象。
    assert object_graph.describe_object(db, ObjectType.WORKSPACE_FILE, "file-1", admin)
    objects[(WorkspaceFile, "file-1")].in_scope = False
    with pytest.raises(ProblemException) as denied:
        object_graph.describe_object(db, ObjectType.WORKSPACE_FILE, "file-1", staff)
    assert denied.value.code == "OBJECT_NOT_FOUND"

    objects[(TopicSpace, "topic-1")].owner_id = "other"
    with pytest.raises(ProblemException):
        object_graph.describe_object(db, ObjectType.TOPIC, "topic-1", staff)


def test_visible_links_filters_inaccessible_related_objects(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    task = SimpleNamespace(id="task-1", title="当前事项")
    report = SimpleNamespace(id="report-1", title="可见报告")
    db = _ObjectDb({(Task, "task-1"): task, (PeriodReport, "report-1"): report})
    monkeypatch.setattr(object_graph, "can_view_task", lambda *_args: True)
    common = {
        "link_type": LinkType.RELATES_TO,
        "note": "关联说明",
        "version": 1,
        "created_by": "user-1",
        "created_at": datetime.now(timezone.utc),
    }
    db.links = [
        SimpleNamespace(
            id="outgoing",
            source_type=ObjectType.TASK,
            source_id="task-1",
            target_type=ObjectType.PERIOD_REPORT,
            target_id="report-1",
            **common,
        ),
        SimpleNamespace(
            id="incoming",
            source_type=ObjectType.PERIOD_REPORT,
            source_id="report-1",
            target_type=ObjectType.TASK,
            target_id="task-1",
            **common,
        ),
        SimpleNamespace(
            id="hidden",
            source_type=ObjectType.TASK,
            source_id="task-1",
            target_type=ObjectType.CONTACT,
            target_id="missing",
            **common,
        ),
    ]
    result = object_graph.visible_links(db, ObjectType.TASK, "task-1", user)
    assert [(item["id"], item["direction"]) for item in result] == [
        ("outgoing", "outgoing"),
        ("incoming", "incoming"),
    ]


class _RuleDb:
    def __init__(self, scalar_batches: list[list[object]]):
        self.scalar_batches = iter(scalar_batches)
        self.added: list[object] = []
        self.existing: set[str] = set()

    def scalars(self, _statement):
        return _ScalarRows(next(self.scalar_batches))

    def scalar(self, statement):
        del statement
        return None

    def add(self, value):
        self.added.append(value)


def test_automation_rules_cover_file_due_and_overdue_suggestions() -> None:
    owner = "owner-1"
    admin = SimpleNamespace(id="admin-1")
    rules = [
        SimpleNamespace(
            id="file-rule",
            name="材料归档",
            trigger="workspace_file_indexed",
            owner_id=owner,
            conditions={"name_contains": "报告", "path_contains": "年度", "extensions": ["PDF"]},
            actions={"task_title": "复核报告", "material_category": "正式稿", "tags": ["年度", "归档"]},
        ),
        SimpleNamespace(
            id="overdue-rule",
            name="逾期提醒",
            trigger="task_overdue",
            owner_id=owner,
            conditions={"days": 3, "statuses": ["in_progress"]},
            actions={"type": "archive_suggestion"},
        ),
        SimpleNamespace(
            id="due-rule",
            name="到期提醒",
            trigger="task_due_soon",
            owner_id=owner,
            conditions={"days": 2},
            actions={"type": "notify"},
        ),
        SimpleNamespace(
            id="ignored-rule",
            name="未知规则",
            trigger="unsafe_action",
            owner_id=owner,
            conditions={},
            actions={},
        ),
    ]
    files = [
        SimpleNamespace(
            id="file-1",
            name="年度工作报告.pdf",
            relative_path="2026/年度/年度工作报告.pdf",
            extension="pdf",
            version=2,
        ),
        SimpleNamespace(
            id="file-2", name="说明.txt", relative_path="其他/说明.txt", extension="txt", version=1
        ),
    ]
    overdue = [SimpleNamespace(id="task-1", title="逾期事项", owner_id=None)]
    due = [SimpleNamespace(id="task-2", title="即将到期事项", owner_id=owner)]
    db = _RuleDb([rules, files, overdue, due])

    scheduler.run_automation_rules(db, admin)

    notifications = [item for item in db.added if isinstance(item, Notification)]
    assert len(notifications) == 3
    assert notifications[0].notification_type == "archive_suggestion"
    assert "建议事项：复核报告" in notifications[0].body
    assert "标签：年度、归档" in notifications[0].body
    assert notifications[1].title == "建议归档"
    assert notifications[1].user_id == admin.id
    assert notifications[2].title == "任务即将截止"
    assert notifications[2].user_id == owner


def test_scheduler_file_helpers_handle_regular_missing_and_io_error(tmp_path, monkeypatch) -> None:
    logger = logging.getLogger("partyops.tests.retention")
    regular = tmp_path / "old.tmp"
    regular.write_text("expired", encoding="utf-8")
    assert scheduler._remove_file(regular, logger) is True
    assert scheduler._remove_file(tmp_path / "missing.tmp", logger) is False
    assert scheduler._remove_file(tmp_path, logger) is False

    broken = tmp_path / "broken.tmp"
    broken.write_text("locked", encoding="utf-8")
    real_unlink = Path.unlink

    def guarded_unlink(path: Path, *args, **kwargs):
        if path == broken:
            raise OSError("file locked")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    assert scheduler._remove_file(broken, logger) is False
    assert scheduler._affected_rows(SimpleNamespace(rowcount=-3)) == 0
    assert scheduler._affected_rows(SimpleNamespace(rowcount=None)) == 0


def test_cleanup_transfer_storage_removes_old_orphans_and_keeps_recent(tmp_path, monkeypatch) -> None:
    old = tmp_path / "orphan.part"
    recent = tmp_path / "recent.part"
    old.write_bytes(b"old")
    recent.write_bytes(b"new")
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    import os

    os.utime(old, (old_time, old_time))

    class _TransfersDb:
        def scalars(self, _statement):
            return _ScalarRows([])

    settings = SimpleNamespace(transfers_dir=tmp_path)
    assert scheduler.cleanup_transfer_storage(_TransfersDb(), settings) == 1
    assert not old.exists()
    assert recent.exists()
