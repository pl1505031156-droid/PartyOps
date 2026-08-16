"""发布门禁最后覆盖：更新环境发现与统一工作日历投影。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import os
import subprocess

from app import calendar_service, update_executor
from app.enums import (
    CalendarEventType,
    ObjectType,
    PeriodReportStatus,
    PeriodType,
    Priority,
    RecurrenceKind,
    TaskStatus,
    UserRole,
)


def test_update_platform_probe_and_dpkg_preflight_fail_closed(monkeypatch) -> None:
    import ctypes

    class _BrokenKernel:
        @staticmethod
        def OpenProcess(*_args):
            raise OSError("process table unavailable")

    # 不能修改全局 os.name；失败断言发生时 pathlib 会在 Windows 上被误导
    # 为 PosixPath，进而掩盖真正的测试失败。
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(name="nt", getpid=os.getpid),
    )
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(kernel32=_BrokenKernel()),
        raising=False,
    )
    assert update_executor._process_is_running(os.getpid()) is True
    assert update_executor._process_is_running(999_999) is False

    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(name="posix", getpid=os.getpid),
    )
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "arm64")
    assert update_executor._architecture() == "arm64"
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "riscv64")
    try:
        update_executor._architecture()
    except RuntimeError as exc:
        assert "支持范围" in str(exc)
    else:
        raise AssertionError("未知架构必须被拒绝")

    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "locked"),
    )
    assert update_executor._ensure_dpkg_ready() is False
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert update_executor._ensure_dpkg_ready() is True


class _FakePath:
    def __init__(self, value):
        self.value = str(value).replace("\\", "/")

    def __str__(self):
        return self.value

    def __repr__(self):
        return f"_FakePath({self.value!r})"

    def __eq__(self, other):
        return isinstance(other, _FakePath) and self.value == other.value

    def __hash__(self):
        return hash(self.value)

    @property
    def parents(self):
        parts = self.value.strip("/").split("/")
        return tuple(_FakePath("/" + "/".join(parts[:index])) for index in range(len(parts) - 1, 0, -1))

    def is_dir(self):
        return self.value in {"/home", "/data/home"}

    def glob(self, _pattern):
        if self.value == "/home":
            return iter(
                [
                    _FakePath("/home/alice/.config/partyops/partyops.env"),
                    _FakePath("/home/bob/.config/partyops/partyops.env"),
                    _FakePath("/home/error/.config/partyops/partyops.env"),
                ]
            )
        return iter([_FakePath("/data/home/carol/.config/partyops/partyops.env")])

    def is_absolute(self):
        return self.value.startswith("/")

    def resolve(self, strict=False):
        del strict
        if self.value == "/home/error/data":
            raise OSError("mount unavailable")
        return self


def test_candidate_host_environments_filters_client_relative_invalid_duplicate_and_defaults(monkeypatch) -> None:
    values = {
        "/etc/partyops/partyops.env": {
            "PARTYOPS_PORT": "18765",
            "PARTYOPS_UPDATE_PUBLIC_KEY": "attacker-controlled",
        },
        "/home/alice/.config/partyops/partyops.env": {
            "PARTYOPS_MODE": "host",
            "PARTYOPS_DATA_DIR": "/home/alice/data",
        },
        "/home/bob/.config/partyops/partyops.env": {
            "PARTYOPS_MODE": "client",
            "PARTYOPS_DATA_DIR": "/home/bob/data",
        },
        "/home/error/.config/partyops/partyops.env": {
            "PARTYOPS_MODE": "host",
            "PARTYOPS_DATA_DIR": "/home/error/data",
        },
        "/data/home/carol/.config/partyops/partyops.env": {
            "PARTYOPS_MODE": "host",
            "PARTYOPS_DATA_DIR": "relative/data",
        },
    }
    monkeypatch.setattr(update_executor, "Path", _FakePath)
    monkeypatch.setattr(
        update_executor,
        "_read_environment",
        lambda path: dict(values.get(str(path), {})),
    )
    monkeypatch.setattr(
        update_executor,
        "_trusted_system_environment_file",
        lambda _path: True,
    )
    result = update_executor._candidate_host_environments()
    assert [item["PARTYOPS_DATA_DIR"] for item in result] == ["/var/lib/partyops"]
    assert all(item["PARTYOPS_ENVIRONMENT"] == "production" for item in result)
    assert all(item["PARTYOPS_STRICT_SQLITE"] == "true" for item in result)
    assert result[0]["PARTYOPS_PORT"] == "18765"
    assert "PARTYOPS_UPDATE_PUBLIC_KEY" not in result[0]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _CalendarDb:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.added = []
        self.flushes = 0

    def scalars(self, _statement):
        return _Rows(next(self.batches))

    def scalar(self, _statement):
        return None

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushes += 1


def test_calendar_preference_topics_and_every_projection_kind(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    preference_db = _CalendarDb([])
    preference = calendar_service.preference_for(preference_db, user)
    assert preference.user_id == user.id and preference_db.added == [preference]
    assert calendar_service._topic_ids(preference_db, []) == {}

    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=31)
    task = SimpleNamespace(
        id="task-1",
        title="完成协同发布",
        owner_id=user.id,
        work_area="党建",
        status=TaskStatus.IN_PROGRESS,
        priority=Priority.HIGH,
        formal_due_at=start + timedelta(days=3),
        internal_due_at=start + timedelta(days=2),
        planned_start_at=start + timedelta(days=1),
        planned_end_at=start + timedelta(days=4),
    )
    topic_link = SimpleNamespace(target_id=task.id, source_id="topic-1")
    recurrence = SimpleNamespace(
        id="rule-1",
        name="每月检查",
        next_run_at=start + timedelta(days=5),
        paused_until=None,
        owner_id=user.id,
        kind=RecurrenceKind.MONTHLY,
    )
    report = SimpleNamespace(
        id="report-1",
        title="八月月报",
        end_at=start + timedelta(days=20),
        status=PeriodReportStatus.DRAFT,
        updated_by=user.id,
        period_type=PeriodType.MONTH,
    )
    notification = SimpleNamespace(
        id="notice-1",
        title="材料待复核",
        created_at=start + timedelta(days=6),
        entity_type="task",
        entity_id=task.id,
        read_at=None,
    )
    holiday = SimpleNamespace(
        id="holiday-1",
        date_key="2026-08-15",
        title="调休工作日",
        is_workday=True,
        kind="adjusted_workday",
        owner_id=user.id,
        note="周末调休",
    )
    db = _CalendarDb([[topic_link], [recurrence], [report], [notification], [holiday]])
    monkeypatch.setattr(calendar_service, "visible_tasks", lambda _db, _user: [task])
    result = calendar_service.calendar_events(
        db,
        user,
        start,
        end,
        owner_ids={user.id},
        work_areas={"党建"},
        topic_ids={"topic-1"},
    )
    kinds = {item["event_type"] for item in result}
    assert {
        CalendarEventType.TASK_DUE,
        CalendarEventType.TASK_PLAN,
        CalendarEventType.RECURRENCE,
        CalendarEventType.REPORT_BOUNDARY,
        CalendarEventType.REMINDER,
        CalendarEventType.ADJUSTED_WORKDAY,
    }.issubset(kinds)
    assert any(item["all_day"] and item["editable"] is False for item in result)


def test_calendar_filters_out_owner_range_and_disabled_event_types(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.ADMIN)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    task = SimpleNamespace(
        id="task-2", title="其他人的事项", owner_id="other", work_area="其他",
        status=TaskStatus.COMPLETED, priority=Priority.NORMAL,
        formal_due_at=start + timedelta(days=2), internal_due_at=None,
        planned_start_at=None, planned_end_at=None,
    )
    db = _CalendarDb([[], []])
    monkeypatch.setattr(calendar_service, "visible_tasks", lambda _db, _user: [task])
    result = calendar_service.calendar_events(
        db,
        user,
        start,
        start + timedelta(days=10),
        event_types={CalendarEventType.HOLIDAY},
        owner_ids={user.id},
    )
    assert result == []
