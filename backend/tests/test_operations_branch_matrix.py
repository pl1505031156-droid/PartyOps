"""周期报告、工作日志与通知的权限和异常分支回归。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.enums import PeriodReportStatus, UserRole
from app.models import (
    ArchiveTemplate,
    Notification,
    PeriodReport,
    PeriodReportItem,
    ReportTemplate,
    Task,
    WorkJournalEntry,
)
from app.problems import ProblemException
from app.routers import operations


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, objects=None, rows=None, scalars=None) -> None:
        self.objects = objects or {}
        self.rows = list(rows or [])
        self.scalar_values = list(scalars or [])
        self.added = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalars(self, _query):
        return Rows(self.rows.pop(0) if self.rows else [])

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, item) -> None:
        self.added.append(item)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def refresh(self, _item) -> None:
        return None


def request():
    return SimpleNamespace(client=None)


def user(role=UserRole.STAFF):
    return SimpleNamespace(id="user-1", role=role)


def assert_problem(code: str, call) -> None:
    with pytest.raises(ProblemException) as error:
        call()
    assert error.value.code == code


def test_version_client_and_report_state_guards() -> None:
    assert operations.client_ip(request()) == ""
    assert operations.client_ip(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))) == "127.0.0.1"
    assert_problem("IF_MATCH_REQUIRED", lambda: operations.parse_version(None))
    assert_problem("IF_MATCH_INVALID", lambda: operations.parse_version("bad"))
    assert operations.parse_version('"7"') == 7
    operations.require_version(7, '"7"')
    assert_problem("VERSION_CONFLICT", lambda: operations.require_version(8, '"7"'))
    operations.require_report_draft(SimpleNamespace(status=PeriodReportStatus.DRAFT), "修改")
    assert_problem(
        "REPORT_LOCKED",
        lambda: operations.require_report_draft(SimpleNamespace(status=PeriodReportStatus.LOCKED), "修改"),
    )
    assert_problem(
        "REPORT_PUBLISHED",
        lambda: operations.require_report_draft(SimpleNamespace(status=PeriodReportStatus.PUBLISHED), "修改"),
    )


def test_report_lists_and_missing_object_guards(monkeypatch) -> None:
    monkeypatch.setattr(operations, "report_to_out", lambda _db, item: {"id": item.id})
    assert operations.list_period_reports(None, 10, user(), Db(rows=[[]])) == []
    assert operations.list_period_reports(SimpleNamespace(value="month"), 10, user(), Db(rows=[[SimpleNamespace(id="r1")]])) == [{"id": "r1"}]

    missing_db = Db()
    calls = [
        lambda: operations.get_period_report("missing", user(), missing_db),
        lambda: operations.patch_period_report("missing", SimpleNamespace(), request(), '"1"', user(), missing_db),
        lambda: operations.period_report_action("missing", SimpleNamespace(action="publish"), request(), '"1"', user(), missing_db),
        lambda: operations.add_period_report_item("missing", SimpleNamespace(), request(), '"1"', user(), missing_db),
        lambda: operations.patch_period_report_item("missing", "item", SimpleNamespace(), request(), '"1"', user(), missing_db),
        lambda: operations.delete_period_report_item("missing", "item", request(), '"1"', user(), missing_db),
        lambda: operations.download_period_docx("missing", user(), missing_db),
        lambda: operations.download_period_xlsx("missing", user(), missing_db),
    ]
    expected = ["PERIOD_REPORT_NOT_FOUND"] * 4 + ["REPORT_ITEM_NOT_FOUND"] * 2 + ["PERIOD_REPORT_NOT_FOUND"] * 2
    for call, code in zip(calls, expected, strict=True):
        assert_problem(code, call)


def test_report_action_and_task_relation_permission_guards(monkeypatch) -> None:
    staff = user()
    for action in ("lock", "reopen"):
        report = SimpleNamespace(id="r1", version=1, status=PeriodReportStatus.DRAFT)
        db = Db(objects={(PeriodReport, "r1"): report})
        assert_problem(
            "ADMIN_REQUIRED",
            lambda action=action, db=db: operations.period_report_action(
                "r1", SimpleNamespace(action=action), request(), '"1"', staff, db
            ),
        )

    report = SimpleNamespace(id="r1", version=1, status=PeriodReportStatus.DRAFT)
    db = Db(objects={(PeriodReport, "r1"): report})
    payload = SimpleNamespace(source_type="task", source_id="missing")
    assert_problem(
        "TASK_NOT_FOUND",
        lambda: operations.add_period_report_item("r1", payload, request(), '"1"', staff, db),
    )
    hidden = SimpleNamespace(id="task-1")
    db = Db(objects={(PeriodReport, "r1"): report, (Task, "task-1"): hidden})
    payload = SimpleNamespace(source_type="task", source_id="task-1")
    monkeypatch.setattr(operations, "can_view_task", lambda *_a: False)
    assert_problem(
        "TASK_NOT_FOUND",
        lambda: operations.add_period_report_item("r1", payload, request(), '"1"', staff, db),
    )


def test_template_duplicate_edit_and_archive_duplicate_guards(monkeypatch) -> None:
    assert_problem(
        "TEMPLATE_EXISTS",
        lambda: operations.create_report_template(SimpleNamespace(name="月报"), request(), user(), Db(scalars=["exists"])),
    )
    assert_problem(
        "TEMPLATE_EXISTS",
        lambda: operations.create_archive_template(SimpleNamespace(name="归档"), request(), user(), Db(scalars=["exists"])),
    )
    assert_problem(
        "REPORT_TEMPLATE_NOT_FOUND",
        lambda: operations.patch_report_template("missing", SimpleNamespace(), request(), '"1"', user(), Db()),
    )
    template = SimpleNamespace(id="t1", created_by="other", version=1)
    db = Db(objects={(ReportTemplate, "t1"): template})
    assert_problem(
        "REPORT_TEMPLATE_EDIT_DENIED",
        lambda: operations.patch_report_template("t1", SimpleNamespace(), request(), '"1"', user(), db),
    )
    template.created_by = "user-1"
    assert_problem(
        "VERSION_CONFLICT",
        lambda: operations.patch_report_template("t1", SimpleNamespace(), request(), '"2"', user(), db),
    )

    monkeypatch.setattr(operations, "write_audit", lambda *_a, **_k: None)
    payload = SimpleNamespace(model_fields_set={"description", "sections"}, description="说明", sections=None)
    assert operations.patch_report_template("t1", payload, request(), '"1"', user(), db).version == 2


def test_journal_visibility_filters_and_permission_guards(monkeypatch) -> None:
    staff = user()
    assert operations.journal_visible(Db(), SimpleNamespace(task_id=None), staff)
    assert not operations.journal_visible(Db(), SimpleNamespace(task_id="missing"), staff)
    task = SimpleNamespace(id="task-1")
    monkeypatch.setattr(operations, "can_view_task", lambda *_a: True)
    assert operations.journal_visible(Db(objects={(Task, "task-1"): task}), SimpleNamespace(task_id="task-1"), staff)

    entry = SimpleNamespace(id="j1", task_id=None)
    monkeypatch.setattr(operations, "journal_to_out", lambda _db, item: {"id": item.id})
    result = operations.list_work_journal(
        "task-1", "user-1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2027, 1, 1, tzinfo=timezone.utc), 10, staff, Db(rows=[[entry]])
    )
    assert result == [{"id": "j1"}]

    assert_problem(
        "TASK_NOT_FOUND",
        lambda: operations.create_work_journal(SimpleNamespace(task_id="missing"), request(), staff, Db()),
    )
    assert_problem(
        "JOURNAL_NOT_FOUND",
        lambda: operations.patch_work_journal("missing", SimpleNamespace(), request(), '"1"', staff, Db()),
    )
    assert_problem(
        "JOURNAL_NOT_FOUND",
        lambda: operations.work_journal_history("missing", staff, Db()),
    )
    immutable = SimpleNamespace(id="j1", task_id=None, immutable=True, created_by="user-1")
    assert_problem(
        "JOURNAL_IMMUTABLE",
        lambda: operations.patch_work_journal("j1", SimpleNamespace(), request(), '"1"', staff, Db(objects={(WorkJournalEntry, "j1"): immutable})),
    )
    foreign = SimpleNamespace(id="j2", task_id=None, immutable=False, created_by="other")
    assert_problem(
        "JOURNAL_EDIT_DENIED",
        lambda: operations.patch_work_journal("j2", SimpleNamespace(), request(), '"1"', staff, Db(objects={(WorkJournalEntry, "j2"): foreign})),
    )


def test_notification_filter_and_ownership_branches() -> None:
    staff = user()
    assert operations.list_notifications(False, None, 10, staff, Db(rows=[[]])) == []
    item = SimpleNamespace(id="n1", user_id="user-1", read_at=None)
    assert operations.list_notifications(True, "mention", 10, staff, Db(rows=[[item]])) == [item]
    assert_problem("NOTIFICATION_NOT_FOUND", lambda: operations.read_notification("missing", staff, Db()))
    foreign = SimpleNamespace(id="n2", user_id="other", read_at=None)
    assert_problem(
        "NOTIFICATION_NOT_FOUND",
        lambda: operations.read_notification("n2", staff, Db(objects={(Notification, "n2"): foreign})),
    )
