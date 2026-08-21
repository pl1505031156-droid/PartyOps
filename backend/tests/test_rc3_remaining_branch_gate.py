"""rc.3 发布前剩余防御分支的定向回归。

这些用例覆盖时间边界、跨平台地址、权限拒绝和旧数据兼容等低频路径；
目的是验证保护条件，不通过排除生产代码来抬高覆盖率。
"""

from __future__ import annotations

import json
import os
import urllib.error
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app import (
    client_agent,
    local_secrets,
    login_throttle,
    networking,
    notifications,
    object_graph,
    projections,
    reports,
    schemas,
    task_service,
    upgrades,
    windows_host_status,
    workspace_access,
)
from app.enums import (
    ArtLevel,
    CalendarEventType,
    PeriodType,
    ReportSection,
    SeasonTheme,
    TaskStatus,
    TaskType,
    UserRole,
)
from app.problems import ProblemException
from app.routers import appearance as appearance_router
from app.routers import auth, calendar, fleet, operations, productivity, support, tasks
from app.routers import workspace as workspace_router
from app.schemas import (
    AdminAppearancePatch,
    CalendarPreferencePatch,
    PeriodReportCreate,
    ReminderPreferencePatch,
    WorkCalendarImport,
    WorkCalendarImportItem,
)


class _Scalars:
    def __init__(self, values=()) -> None:
        self.values = list(values)

    def all(self):
        return list(self.values)

    def one_or_none(self):
        return self.values[0] if self.values else None


class _Db:
    def __init__(
        self,
        *,
        objects=None,
        scalar_values=(),
        scalars_values=(),
        execute_values=(),
    ) -> None:
        self.objects = objects or {}
        self.scalar_values = list(scalar_values)
        self.scalars_values = list(scalars_values)
        self.execute_values = list(execute_values)
        self.added = []
        self.deleted = []
        self.commits = 0

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def scalars(self, _statement):
        values = self.scalars_values.pop(0) if self.scalars_values else []
        return _Scalars(values)

    def execute(self, *_args, **_kwargs):
        values = self.execute_values.pop(0) if self.execute_values else []
        if isinstance(values, BaseException):
            raise values
        if hasattr(values, "rowcount"):
            return values
        return _Scalars(values)

    def add(self, value):
        self.added.append(value)

    def delete(self, value):
        self.deleted.append(value)

    def flush(self):
        return None

    def commit(self):
        self.commits += 1

    def refresh(self, _value):
        return None

    def query(self, _model):
        class Query:
            def filter(self, *_args):
                return self

            def delete(self, **_kwargs):
                return 1

        return Query()


def _request(host: str = "127.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": (host, 12345),
            "server": ("partyops", 18765),
            "scheme": "https",
            "query_string": b"",
        }
    )


def test_report_section_invalid_duplicate_and_empty_fallback() -> None:
    report = SimpleNamespace(
        snapshot={
            "design": {
                "sections": [
                    ReportSection.COMPLETED.value,
                    "invalid",
                    ReportSection.COMPLETED.value,
                    ReportSection.RISK.value,
                ]
            }
        }
    )
    assert reports.report_sections(report) == [ReportSection.COMPLETED, ReportSection.RISK]
    assert reports.report_sections(SimpleNamespace(snapshot={})) == list(ReportSection)


def test_projection_naive_backoff_and_empty_anchors() -> None:
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    checkpoint = SimpleNamespace(
        status="failed",
        last_run_at=now.replace(tzinfo=None),
        failed_count=1,
    )
    assert projections._backoff_active(checkpoint, now + timedelta(seconds=30))
    assert projections._task_anchors(None) == []
    task = SimpleNamespace(
        completed_at=now,
        planned_start_at=None,
        planned_end_at=now + timedelta(hours=1),
        internal_due_at=None,
        formal_due_at=None,
    )
    assert projections._task_anchors(task) == [now, now + timedelta(hours=1)]


def test_network_bind_advertise_policy_matrix() -> None:
    with pytest.raises(RuntimeError, match="明确"):
        networking.validate_bind_host("0.0.0.0", True, advertised_host="")
    # 固定设备名交由系统解析，不把无法解析误报成公网地址。
    networking.validate_bind_host("0.0.0.0", True, advertised_host="office-host")
    networking.validate_bind_host("0.0.0.0", True, advertised_host="192.168.10.8")
    with pytest.raises(RuntimeError, match="公网"):
        networking.validate_bind_host("0.0.0.0", True, advertised_host="8.8.8.8")
    with pytest.raises(RuntimeError, match="链路本地"):
        networking.validate_bind_host("0.0.0.0", True, advertised_host="169.254.1.2")


def test_windows_health_invalid_payload_and_partial_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    log = windows_host_status.service_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("被截断的首行\n完整末行", encoding="utf-8")
    assert windows_host_status.tail_service_log(tmp_path, max_bytes=14) == "完整末行"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{}'

    monkeypatch.setattr(windows_host_status.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert windows_host_status.probe_loopback_health(18765, tls=False) == (
        False,
        "健康检查返回内容无效",
    )


def test_local_secret_rejects_valid_encrypted_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        local_secrets,
        "_fernet",
        lambda: SimpleNamespace(decrypt=lambda _value: json.dumps(["not", "object"]).encode()),
    )
    with pytest.raises(ValueError, match="格式无效"):
        local_secrets.decrypt_local_json("token")


def test_login_throttle_prunes_stale_entries() -> None:
    throttle = login_throttle.LoginThrottle()
    throttle._states["stale"] = login_throttle._FailureState(1, 0, 0, 0)
    settings = SimpleNamespace(
        login_window_seconds=10,
        login_lock_seconds=10,
        login_throttle_max_entries=10,
    )
    throttle._prune(100, settings)
    assert throttle._states == {}


def test_workspace_host_root_denies_disabled_or_untrusted_device() -> None:
    staff = SimpleNamespace(id="u1", role=SimpleNamespace(value="staff"))
    disabled = SimpleNamespace(enabled=False, source=SimpleNamespace(value="host"))
    assert not any(workspace_access.workspace_root_permissions(_Db(), disabled, staff).values())

    host = SimpleNamespace(enabled=True, source=SimpleNamespace(value="host"))
    denied = workspace_access.workspace_root_permissions(_Db(), host, staff, "missing")
    assert denied["browse"] is False
    device = SimpleNamespace(active=True, status="online", allow_host_access=True)
    allowed = workspace_access.workspace_root_permissions(
        _Db(objects={"device": device}), host, staff, "device"
    )
    assert allowed["browse"] is True and allowed["manage_root"] is False


def test_object_descriptors_cover_denied_and_optional_types(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(id="u1", role=UserRole.STAFF)
    task = SimpleNamespace(id="task", title="事项")
    db = _Db(objects={"task": task})
    monkeypatch.setattr(object_graph, "can_view_task", lambda *_a: True)
    assert object_graph.describe_object(db, "task", "task", user).title == "事项"
    monkeypatch.setattr(object_graph, "can_view_task", lambda *_a: False)
    with pytest.raises(ProblemException):
        object_graph.describe_object(db, "task", "task", user)

    for object_type, value, label in (
        ("period_report", SimpleNamespace(id="report", title="报告"), "报告"),
        ("knowledge", SimpleNamespace(id="knowledge", title="知识"), "知识"),
        ("contact", SimpleNamespace(id="contact", name="联系人"), "联系人"),
        ("topic", SimpleNamespace(id="topic", name="专题", owner_id="u1"), "专题"),
    ):
        assert object_graph.describe_object(_Db(objects={label if False else value.id: value}), object_type, value.id, user).title == label


def test_calendar_range_filters_preference_and_workday_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="u1")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ProblemException) as invalid:
        calendar.list_calendar_events(start, start, [], [], [], [], user, _Db())
    assert invalid.value.code == "CALENDAR_RANGE_INVALID"
    with pytest.raises(ProblemException) as too_large:
        calendar.list_calendar_events(start, start + timedelta(days=371), [], [], [], [], user, _Db())
    assert too_large.value.code == "CALENDAR_RANGE_TOO_LARGE"

    captured = {}
    monkeypatch.setattr(
        calendar,
        "calendar_events",
        lambda *_a, **kwargs: captured.update(kwargs) or [],
    )
    assert calendar.list_calendar_events(
        start,
        start + timedelta(days=1),
        [CalendarEventType.TASK_DUE],
        ["u1"],
        ["机关"],
        ["topic"],
        user,
        _Db(),
    ) == []
    assert captured["event_types"] == {CalendarEventType.TASK_DUE}

    preference = SimpleNamespace(version=2, visible_event_types=[], week_starts_monday=True)
    monkeypatch.setattr(calendar, "preference_for", lambda *_a: preference)
    with pytest.raises(ProblemException) as conflict:
        calendar.patch_calendar_preferences(
            CalendarPreferencePatch(week_starts_monday=False), '"1"', user, _Db()
        )
    assert conflict.value.code == "VERSION_CONFLICT"

    assert calendar.list_calendar_workdays(None, user, _Db(scalars_values=[[]])) == []
    assert calendar.list_calendar_workdays(2026, user, _Db(scalars_values=[[]])) == []


def test_calendar_import_creates_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calendar, "write_audit", lambda *_a, **_k: None)
    existing = SimpleNamespace(title="旧", is_workday=False, note="", version=1)
    payload = WorkCalendarImport(
        items=[
            WorkCalendarImportItem(
                date_key="2026-10-01", kind="holiday", title="国庆节", is_workday=False, note=""
            ),
            WorkCalendarImportItem(
                    date_key="2026-10-10", kind="adjusted_workday", title="调休", is_workday=True, note="补班"
            ),
        ]
    )
    db = _Db(scalar_values=[None, existing])
    result = calendar.import_calendar_workdays(payload, SimpleNamespace(id="admin"), db)
    assert len(result) == 2 and db.added and existing.title == "调休" and existing.version == 2


def test_operations_rejects_missing_inactive_or_mismatched_template() -> None:
    payload = PeriodReportCreate(template_id="t1", period_type=PeriodType.MONTH)
    with pytest.raises(ProblemException) as missing:
        operations.create_period_report(payload, _request(), SimpleNamespace(id="u"), _Db())
    assert missing.value.code == "REPORT_TEMPLATE_NOT_FOUND"
    template = SimpleNamespace(active=True, period_type=PeriodType.WEEK)
    with pytest.raises(ProblemException) as mismatch:
        operations.create_period_report(
            payload, _request(), SimpleNamespace(id="u"), _Db(objects={"t1": template})
        )
    assert mismatch.value.code == "REPORT_TEMPLATE_PERIOD_MISMATCH"


def test_admin_appearance_conflict_and_existing_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appearance_router, "global_appearance", lambda _db: {"version": 3})
    payload = AdminAppearancePatch(
        theme_mode="fixed",
        fixed_theme=SeasonTheme.SPRING,
        default_art_level=ArtLevel.STANDARD,
    )
    with pytest.raises(ProblemException):
        appearance_router.patch_admin_appearance(
            payload,
            _request(),
            '"2"',
            SimpleNamespace(id="admin"),
            _Db(),
        )

    setting = SimpleNamespace(value={})
    db = _Db(objects={appearance_router.GLOBAL_APPEARANCE_KEY: setting})
    monkeypatch.setattr(appearance_router, "write_audit", lambda *_a, **_k: None)
    result = appearance_router.patch_admin_appearance(
        payload, _request(), '"3"', SimpleNamespace(id="admin"), db
    )
    assert result.version == 4 and setting.value["theme_mode"] == "fixed"


def test_auth_ipv4_mapped_loopback_and_untrusted_bootstrap() -> None:
    assert auth.bootstrap_request_is_local(_request("::ffff:127.0.0.1"))
    request = _request("10.0.0.8")
    with pytest.raises(ProblemException) as denied:
        auth.bootstrap_host(
            SimpleNamespace(username="admin", display_name="管理员", password="secret"),
            request,
            _Db(),
        )
    assert denied.value.code == "BOOTSTRAP_TRUST_REQUIRED"


def test_upgrade_missing_database_short_circuits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        upgrades,
        "get_settings",
        lambda: SimpleNamespace(database_path=tmp_path / "missing.db"),
    )
    assert upgrades.database_has_business_data() is False


def test_support_parse_role_missing_and_conflict_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    staff = SimpleNamespace(id="staff", role=UserRole.STAFF)
    with pytest.raises(ProblemException) as missing_header:
        support.parse_if_match(None)
    assert missing_header.value.code == "IF_MATCH_REQUIRED"

    # 管理员显式要求包含停用模板时不再追加 active 过滤条件。
    assert support.list_templates(True, admin, _Db(scalars_values=[[]])) == []

    with pytest.raises(ProblemException) as template_role:
        support.update_template("missing", SimpleNamespace(), _request(), '"1"', staff, _Db())
    assert template_role.value.code == "ADMIN_REQUIRED"
    template = SimpleNamespace(id="t1", version=1)
    with pytest.raises(ProblemException) as duplicate:
        support.update_template(
                "t1",
                SimpleNamespace(name="重复"),
                _request(),
                '"1"',
            admin,
            _Db(objects={"t1": template}, scalar_values=[SimpleNamespace(id="t2")]),
        )
    assert duplicate.value.code == "TEMPLATE_EXISTS"

    with pytest.raises(ProblemException) as recurrence_role:
        support.create_recurrence(SimpleNamespace(), _request(), staff, _Db())
    assert recurrence_role.value.code == "ADMIN_REQUIRED"
    with pytest.raises(ProblemException) as update_role:
        support.update_recurrence("r1", SimpleNamespace(), _request(), '"1"', staff, _Db())
    assert update_role.value.code == "ADMIN_REQUIRED"
    with pytest.raises(ProblemException) as run_role:
        support.run_recurrences(staff, _Db())
    assert run_role.value.code == "ADMIN_REQUIRED"


def test_support_recurrence_owner_and_contact_validation() -> None:
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    rule = SimpleNamespace(id="r1", version=1)
    owner_payload = SimpleNamespace(
        owner_id="missing",
        contact_ids=None,
        model_fields_set={"owner_id"},
    )
    with pytest.raises(ProblemException) as owner:
        support.update_recurrence(
            "r1", owner_payload, _request(), '"1"', admin, _Db(objects={"r1": rule})
        )
    assert owner.value.code == "OWNER_INVALID"

    contact_payload = SimpleNamespace(
        owner_id=None,
        contact_ids=["c1", "missing"],
        model_fields_set={"contact_ids"},
    )
    with pytest.raises(ProblemException) as contacts:
        support.update_recurrence(
            "r1",
            contact_payload,
            _request(),
            '"1"',
            admin,
            _Db(objects={"r1": rule}, scalars_values=[["c1"]]),
        )
    assert contacts.value.code == "CONTACT_INVALID"


def test_support_knowledge_contact_and_reminder_missing_versions() -> None:
    user = SimpleNamespace(id="u1", role=UserRole.STAFF)
    # 关键词分支只改变 SQL，空结果足以验证不会退化为全表异常。
    assert support.list_knowledge("制度", user, _Db(scalars_values=[[]])) == []
    assert support.list_contacts("组织", user, _Db(scalars_values=[[]])) == []

    for operation, identifier, code in (
        (support.update_knowledge, "k1", "KNOWLEDGE_NOT_FOUND"),
        (support.delete_knowledge, "k1", "KNOWLEDGE_NOT_FOUND"),
        (support.update_contact, "c1", "CONTACT_NOT_FOUND"),
        (support.delete_contact, "c1", "CONTACT_NOT_FOUND"),
    ):
        args = (
            (identifier, SimpleNamespace(), _request(), '"1"', user, _Db())
            if operation in {support.update_knowledge, support.update_contact}
            else (identifier, _request(), '"1"', user, _Db())
        )
        with pytest.raises(ProblemException) as missing:
            operation(*args)
        assert missing.value.code == code

    knowledge = SimpleNamespace(id="k1", version=2)
    contact = SimpleNamespace(id="c1", version=2)
    for operation, item, identifier in (
        (support.update_knowledge, knowledge, "k1"),
        (support.delete_knowledge, knowledge, "k1"),
        (support.update_contact, contact, "c1"),
        (support.delete_contact, contact, "c1"),
    ):
        db = _Db(objects={identifier: item})
        args = (
            (identifier, SimpleNamespace(), _request(), '"1"', user, db)
            if operation in {support.update_knowledge, support.update_contact}
            else (identifier, _request(), '"1"', user, db)
        )
        with pytest.raises(ProblemException) as conflict:
            operation(*args)
        assert conflict.value.code == "VERSION_CONFLICT"

    created = support.reminder_preferences(user, _Db())
    assert created.user_id == "u1"
    with pytest.raises(ProblemException) as reminder_conflict:
        support.update_reminder_preferences(
            ReminderPreferencePatch(enabled=False),
            _request(),
            '"0"',
            user,
            _Db(),
        )
    assert reminder_conflict.value.code == "VERSION_CONFLICT"


@pytest.mark.parametrize(
    "smart",
    ["this_week_completed", "unarchived", "finals", "annual_focus"],
)
def test_support_search_filter_and_smart_branches(
    monkeypatch: pytest.MonkeyPatch,
    smart: str,
) -> None:
    monkeypatch.setattr(support, "can_view_task", lambda *_a: True)
    monkeypatch.setattr(support, "task_to_out", lambda *_a, **_k: None)
    scalars_values = []
    execute_values = []
    q = "党建" if smart == "finals" else ""
    if q:
        execute_values.append([("task-1",)])
        scalars_values.append(["task-2"])
    if smart == "finals":
        scalars_values.append(["task-1"])
    scalars_values.append([])
    result = support.search(
        q=q,
        year=2026,
        category="制度",
        owner_id="u1",
        status=TaskStatus.COMPLETED,
        file_name=None,
        smart=smart,
        page=1,
        page_size=10,
        user=SimpleNamespace(id="u1"),
        db=_Db(scalars_values=scalars_values, execute_values=execute_values),
    )
    assert result.total == 0


def test_support_search_fts_failure_filename_and_no_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(support, "can_view_task", lambda *_a: True)
    result = support.search(
        q="制度",
        year=None,
        category=None,
        owner_id=None,
        status=None,
        file_name="报送稿.docx",
        smart=None,
        page=1,
        page_size=10,
        user=SimpleNamespace(id="u1"),
        db=_Db(
            execute_values=[RuntimeError("FTS unavailable")],
            scalars_values=[[], [], []],
        ),
    )
    assert result.total == 0


def test_agent_response_filename_and_config_validation_matrix(tmp_path: Path) -> None:
    assert client_agent._response_filename("attachment; size=1; filename*=UTF-8''%E5%85%9A.zip") == "党.zip"
    assert client_agent._response_filename('attachment; filename="backup.zip"') == "backup.zip"
    assert client_agent._response_filename("attachment; invalid") == "PartyOps-latest.partyops-backup"
    with pytest.raises(ValueError, match="配对令牌"):
        client_agent.validate_config(
            {"host_url": "https://192.168.1.2:18765", "backup_dir": str(tmp_path)}
        )


def test_agent_enrollment_http_error_payload_matrix() -> None:
    def error(payload: bytes, code: int = 409) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://host/enroll",
            code,
            "error",
            {},
            SimpleNamespace(read=lambda _limit=-1: payload, close=lambda: None),
        )

    assert "无效或已过期" in str(
        client_agent.enrollment_http_error(error(b'{"code":"ENROLLMENT_INVALID"}'))
    )
    assert "已完成" in str(
        client_agent.enrollment_http_error(
            error('{"code":"ENROLLMENT_ALREADY_COMPLETED","detail":"已完成"}'.encode())
        )
    )
    assert "换一个名称" in str(
        client_agent.enrollment_http_error(error(b'{"code":"DEVICE_NAME_EXISTS"}'))
    )
    assert "HTTP 409" in str(client_agent.enrollment_http_error(error(b"[]")))
    assert "HTTP 409" in str(client_agent.enrollment_http_error(error(b"not-json")))


def test_agent_safe_shared_roots_and_resolution_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "共享"
    root.mkdir()
    shared_file = root / "文件.txt"
    shared_file.write_text("data", encoding="utf-8")
    assert client_agent._safe_shared_roots({"shared_roots": "invalid"}) == []
    roots = client_agent._safe_shared_roots(
        {
            "shared_roots": [
                "invalid",
                {"local_path": str(tmp_path / "missing"), "remote_key": "x"},
                {"local_path": str(root), "remote_key": "bad/key"},
                {
                    "local_path": str(root),
                    "remote_key": "root_1",
                    "approval_status": "approved",
                },
            ]
        }
    )
    assert len(roots) == 1
    config = {"device_id": "device", "shared_roots": roots}
    assert client_agent._resolve_shared_path(config, "device:root_1:文件.txt") == shared_file.resolve()
    assert client_agent._resolve_shared_path(
        config, "device:root_1:.", allow_directory=True
    ) == root.resolve()
    with pytest.raises(client_agent.AgentCommandError) as wrong_device:
        client_agent._resolve_shared_path(config, "other:root_1:文件.txt")
    assert wrong_device.value.code == "ROOT_NOT_APPROVED"
    with pytest.raises(client_agent.AgentCommandError) as traversal:
        client_agent._resolve_shared_path(config, "device:root_1:../secret")
    assert traversal.value.code == "PATH_TRAVERSAL_DENIED"
    with pytest.raises(client_agent.AgentCommandError) as unapproved:
        client_agent._resolve_shared_path(config, "device:unknown:文件.txt")
    assert unapproved.value.code == "ROOT_NOT_APPROVED"
    with pytest.raises(client_agent.AgentCommandError) as missing:
        client_agent._resolve_shared_path(config, "device:root_1:missing.txt")
    assert missing.value.code == "SOURCE_MISSING"


def test_agent_sync_roots_changed_and_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    config_path = tmp_path / "config.json"
    config = {
        "shared_roots": [
            {
                "local_path": str(root),
                "remote_key": "root",
                "name": "共享",
                "approval_status": "pending",
            }
        ]
    }
    monkeypatch.setattr(
        client_agent,
        "register_shared_root",
        lambda *_a, **_k: {
            "id": "id1",
            "approval_status": "approved",
            "approval_note": "",
            "enabled": True,
        },
    )
    roots = client_agent.sync_shared_roots("https://host", "token", config, config_path)
    assert roots[0]["root_id"] == "id1" and config_path.is_file()
    monkeypatch.setattr(
        client_agent,
        "register_shared_root",
        lambda *_a, **_k: pytest.fail("已批准目录不应重复登记"),
    )
    assert client_agent.sync_shared_roots("https://host", "token", config, config_path)[0]["enabled"]


def test_agent_certificate_transfer_status_and_bundle_rejections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(client_agent.AgentCommandError) as missing_id:
        client_agent.rotate_device_certificate("https://host", "token", {}, tmp_path / "config.json")
    assert missing_id.value.code == "DEVICE_ID_MISSING"

    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: [])
    with pytest.raises(client_agent.AgentCommandError) as bad_status:
        client_agent.get_transfer_status("https://host", "token", "transfer")
    assert bad_status.value.code == "TRANSFER_INVALID"
    with pytest.raises(client_agent.AgentCommandError) as missing_certificate:
        client_agent.rotate_device_certificate(
            "https://host", "token", {"device_id": "device"}, tmp_path / "config.json"
        )
    assert missing_certificate.value.code == "CERTIFICATE_ROTATION_FAILED"

    with pytest.raises(client_agent.AgentCommandError) as empty:
        client_agent.upload_bundle_transfer(
            "https://host", "token", {"items": []}, {"receive_dir": str(tmp_path)}
        )
    assert empty.value.code == "BUNDLE_ITEMS_INVALID"
    with pytest.raises(client_agent.AgentCommandError) as malformed:
        client_agent.upload_bundle_transfer(
            "https://host",
            "token",
            {"transfer_id": "transfer", "items": ["invalid"]},
            {"receive_dir": str(tmp_path)},
        )
    assert malformed.value.code == "BUNDLE_ITEMS_INVALID"
    with pytest.raises(client_agent.AgentCommandError) as traversal:
        client_agent.upload_bundle_transfer(
            "https://host",
            "token",
            {
                "transfer_id": "transfer",
                "items": [{"remote_file_key": "x", "relative_path": "../escape"}],
            },
            {"receive_dir": str(tmp_path)},
        )
    assert traversal.value.code == "PATH_TRAVERSAL_DENIED"


def test_agent_non_overwriting_target_and_restart_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(client_agent.AgentCommandError):
        client_agent._non_overwriting_target(tmp_path, "..")
    first = client_agent._non_overwriting_target(tmp_path, "报告.docx")
    first.write_text("one", encoding="utf-8")
    assert client_agent._non_overwriting_target(tmp_path, "报告.docx").name == "报告 (1).docx"

    executed = []
    monkeypatch.setattr(client_agent.os, "execv", lambda executable, args: executed.append((executable, args)))
    monkeypatch.setattr(client_agent.sys, "executable", str(tmp_path / "PartyOpsAgent.exe"))
    monkeypatch.setattr(client_agent.sys, "frozen", True, raising=False)
    client_agent._restart_agent_after_update(tmp_path / "config.json")
    assert "-m" not in executed[-1][1]
    monkeypatch.setattr(client_agent.sys, "frozen", False, raising=False)
    client_agent._restart_agent_after_update(tmp_path / "config.json")
    assert "app.client_agent" in executed[-1][1]


def _task_payload(**overrides) -> schemas.TaskCreate:
    values = {
        "title": "覆盖率事项",
        "task_type": "standard",
        "owner_id": "owner",
        "reviewer_id": None,
        "collaborator_ids": [],
        "contact_ids": [],
        "steps": [],
        "materials": [],
    }
    values.update(overrides)
    return schemas.TaskCreate.model_validate(values)


def _task(**overrides):
    values = {
        "id": "task",
        "title": "事项",
        "status": TaskStatus.IN_PROGRESS,
        "sensitivity": "normal",
        "allow_sensitive_content": False,
        "description": "",
        "owner_id": "owner",
        "reviewer_id": None,
        "created_by": "owner",
        "version": 1,
        "completed_at": None,
        "archived_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_task_service_permission_and_create_validation_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="owner", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(task_service, "can_view_task", lambda *_a: False)
    assert not task_service.can_edit_task(_Db(), task, actor)
    assert not task_service.can_manage_task(_Db(), task, actor)

    monkeypatch.setattr(task_service, "can_view_task", lambda *_a: True)
    with pytest.raises(ProblemException) as owner:
        task_service.create_task(_Db(), _task_payload(owner_id="missing"), actor)
    assert owner.value.code == "OWNER_INVALID"

    active_owner = SimpleNamespace(id="owner", active=True)
    with pytest.raises(ProblemException) as reviewer:
        task_service.create_task(
            _Db(objects={"owner": active_owner}),
            _task_payload(reviewer_id="missing"),
            actor,
        )
    assert reviewer.value.code == "REVIEWER_INVALID"

    parent = _task(id="parent", task_type=TaskType.PROJECT, deleted_at=None)
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as parent_denied:
        task_service.create_task(
            _Db(objects={"owner": active_owner, "parent": parent}),
            _task_payload(parent_task_id="parent"),
            actor,
        )
    assert parent_denied.value.code == "PARENT_TASK_DENIED"

    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    with pytest.raises(ProblemException) as contact:
        task_service.create_task(
            _Db(objects={"owner": active_owner}, scalars_values=[["c1"]]),
            _task_payload(contact_ids=["c1", "missing"]),
            actor,
        )
    assert contact.value.code == "CONTACT_INVALID"

    monkeypatch.setattr(task_service, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "record_system_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "emit_event", lambda *_a, **_k: None)
    with pytest.raises(ProblemException) as participant:
        task_service.create_task(
            _Db(objects={"owner": active_owner}),
            _task_payload(start_in_breakdown=True, collaborator_ids=["missing"]),
            actor,
        )
    assert participant.value.code == "PARTICIPANT_INVALID"


def test_task_update_restricted_owner_reviewer_and_contact_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="owner", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as denied:
        task_service.update_task(_Db(), task, schemas.TaskUpdate(title="新标题"), 1, actor)
    assert denied.value.code == "TASK_EDIT_DENIED"

    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "can_manage_task", lambda *_a: True)
    with pytest.raises(ProblemException) as restricted:
        task_service.update_task(
            _Db(),
            task,
            schemas.TaskUpdate(sensitivity="restricted", description="敏感正文"),
            1,
            actor,
        )
    assert restricted.value.code == "RESTRICTED_BODY_DISABLED"

    with pytest.raises(ProblemException) as owner:
        task_service.update_task(
            _Db(), task, schemas.TaskUpdate(owner_id="missing"), 1, actor
        )
    assert owner.value.code == "OWNER_INVALID"
    with pytest.raises(ProblemException) as reviewer:
        task_service.update_task(
            _Db(), task, schemas.TaskUpdate(reviewer_id="missing"), 1, actor
        )
    assert reviewer.value.code == "REVIEWER_INVALID"
    with pytest.raises(ProblemException) as contact:
        task_service.update_task(
            _Db(scalars_values=[["c1"]]),
            task,
            schemas.TaskUpdate(contact_ids=["c1", "missing"]),
            1,
            actor,
        )
    assert contact.value.code == "CONTACT_INVALID"


def test_task_action_rejection_and_optimistic_conflict_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="owner", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as denied:
        task_service.apply_task_action(_Db(), task, "wait_feedback", "", actor)
    assert denied.value.code == "TASK_ACTION_DENIED"

    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "can_manage_task", lambda *_a: True)
    with pytest.raises(ProblemException) as conflict:
        task_service.apply_task_action(
            _Db(), task, "wait_feedback", "", actor, expected_version=2
        )
    assert conflict.value.code == "VERSION_CONFLICT"

    with pytest.raises(ProblemException) as reviewer:
        task_service.apply_task_action(
            _Db(),
            _task(status=TaskStatus.PENDING_REVIEW, reviewer_id="reviewer"),
            "approve",
            "",
            actor,
        )
    assert reviewer.value.code == "REVIEWER_REQUIRED"
    with pytest.raises(ProblemException) as review_required:
        task_service.apply_task_action(
            _Db(), _task(reviewer_id="reviewer"), "complete", "", actor
        )
    assert review_required.value.code == "REVIEW_REQUIRED"
    with pytest.raises(ProblemException) as note_required:
        task_service.apply_task_action(
            _Db(), _task(status=TaskStatus.COMPLETED), "reopen", "  ", actor
        )
    assert note_required.value.code == "ACTION_REASON_REQUIRED"

    db = _Db(execute_values=[SimpleNamespace(rowcount=0)])
    db.rollback = lambda: None
    with pytest.raises(ProblemException) as write_conflict:
        task_service.apply_task_action(db, task, "wait_feedback", "", actor)
    assert write_conflict.value.code == "VERSION_CONFLICT"


def test_task_complete_without_period_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="owner", role=UserRole.ADMIN)
    task = _task()
    db = _Db(scalars_values=[[]], execute_values=[SimpleNamespace(rowcount=1)])
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "can_manage_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "record_system_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "emit_event", lambda *_a, **_k: None)
    import app.reports as report_module

    monkeypatch.setattr(report_module, "ensure_period_reports", lambda *_a, **_k: ([], 0, 0))
    assert task_service.apply_task_action(
        db, task, "complete", "", actor, commit=False
    ) is task


def test_fleet_patch_and_delete_device_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(id="admin")
    revoked = SimpleNamespace(
        id="device-12345678",
        version=1,
        active=False,
        status="revoked",
    )
    monkeypatch.setattr(fleet, "device_is_deleted", lambda _device: False)
    monkeypatch.setattr(fleet, "write_audit", lambda *_a, **_k: None)
    result = fleet.patch_device(
        revoked.id,
        schemas.DevicePatch(active=True),
        _request(),
        '"1"',
        admin,
        _Db(objects={revoked.id: revoked}),
    )
    assert result.status == "offline"

    with pytest.raises(ProblemException) as missing:
        fleet.delete_managed_device("missing", _request(), '"1"', admin, _Db())
    assert missing.value.code == "DEVICE_NOT_FOUND"
    with pytest.raises(ProblemException) as conflict:
        fleet.delete_managed_device(
            revoked.id, _request(), '"0"', admin, _Db(objects={revoked.id: revoked})
        )
    assert conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as active_transfer:
        fleet.delete_managed_device(
            revoked.id,
            _request(),
            '"2"',
            admin,
            _Db(objects={revoked.id: revoked}, scalar_values=[2]),
        )
    assert active_transfer.value.code == "DEVICE_HAS_ACTIVE_TRANSFERS"


def test_fleet_delete_device_cancels_commands_and_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = SimpleNamespace(id="admin")
    device = SimpleNamespace(
        id="device-12345678",
        name="协同机",
        version=1,
        active=True,
        status="offline",
        device_metadata=None,
        allow_host_access=True,
        allow_device_transfer=True,
        agent_token_hash="hash",
        certificate_fingerprint="cert",
    )
    grant = SimpleNamespace(active=True, version=1)
    command = SimpleNamespace(status="queued", result={}, completed_at=None)
    root = SimpleNamespace(enabled=True, approval_status="approved", scan_status="indexed", version=1)
    db = _Db(
        objects={device.id: device},
        scalar_values=[0],
        scalars_values=[[grant], [command], [root]],
    )
    monkeypatch.setattr(fleet, "device_is_deleted", lambda _device: False)
    monkeypatch.setattr(fleet, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(fleet, "emit_event", lambda *_a, **_k: None)
    result = fleet.delete_managed_device(device.id, _request(), '"1"', admin, db)
    assert result["deleted"] is True
    assert command.status == "failed" and root.enabled is False and grant.active is False


def test_fleet_remote_root_and_index_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = SimpleNamespace(id="device", created_by="owner")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_a, **_k: device)
    for operation, args in (
        (
            fleet.rename_device_root,
            ("missing", SimpleNamespace(name="新名称"), _request(), "token", _Db()),
        ),
        (fleet.disable_device_root, ("missing", _request(), "token", _Db())),
    ):
        with pytest.raises(ProblemException) as missing:
            operation(*args)
        assert missing.value.code == "REMOTE_ROOT_NOT_FOUND"

    with pytest.raises(ProblemException) as root_denied:
        fleet.upload_index_delta(SimpleNamespace(root_id="missing"), "token", _Db())
    assert root_denied.value.code == "ROOT_NOT_APPROVED"

    root = SimpleNamespace(
        id="root",
        source=SimpleNamespace(value="device"),
        device_id="device",
        approval_status="approved",
        semantic_content_enabled=False,
        remote_key="remote",
    )
    invalid_file = SimpleNamespace(relative_path="../escape")
    with pytest.raises(ProblemException) as invalid_path:
        fleet.upload_index_delta(
            SimpleNamespace(root_id="root", files=[invalid_file], removed_paths=[]),
            "token",
            _Db(objects={"root": root}),
        )
    assert invalid_path.value.code == "REMOTE_PATH_INVALID"
    parent_escape = SimpleNamespace(relative_path="file.txt", parent_relative_path="../escape")
    with pytest.raises(ProblemException) as invalid_parent:
        fleet.upload_index_delta(
            SimpleNamespace(root_id="root", files=[parent_escape], removed_paths=[]),
            "token",
            _Db(objects={"root": root}),
        )
    assert invalid_parent.value.code == "REMOTE_PATH_INVALID"
    with pytest.raises(ProblemException) as invalid_removed:
        fleet.upload_index_delta(
            SimpleNamespace(root_id="root", files=[], removed_paths=["../escape"]),
            "token",
            _Db(objects={"root": root}),
        )
    assert invalid_removed.value.code == "REMOTE_PATH_INVALID"


def test_fleet_command_concurrency_skips_extra_transfer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = SimpleNamespace(id="device")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_a, **_k: device)
    first = SimpleNamespace(
        id="c1",
        command_type="upload_file",
        status="queued",
        delivered_at=None,
        delivery_attempts=0,
        payload={},
    )
    second = SimpleNamespace(
        id="c2",
        command_type="download_file",
        status="queued",
        delivered_at=None,
        delivery_attempts=0,
        payload={},
    )
    db = _Db(scalars_values=[[first, second]], scalar_values=[0])
    result = fleet.list_commands("token", db)
    assert [item["id"] for item in result] == ["c1"] and second.status == "queued"
    third = SimpleNamespace(
        id="c3",
        command_type="upload_file",
        status="queued",
        delivered_at=None,
        delivery_attempts=0,
        payload={},
    )
    assert fleet.list_commands("token", _Db(scalars_values=[[third]], scalar_values=[2])) == []


def test_fleet_inbox_and_transfer_creation_rejections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transfer = SimpleNamespace(
        id="transfer",
        original_name="missing.txt",
        status="completed",
        direction="device_to_host",
        size_bytes=1,
    )
    monkeypatch.setattr(
        fleet,
        "get_settings",
        lambda: SimpleNamespace(
            inbox_dir=tmp_path,
            transfer_max_file_gb=1,
            transfers_dir=tmp_path,
        ),
    )
    with pytest.raises(ProblemException) as missing:
        fleet._completed_inbox_path(transfer)
    assert missing.value.code == "INBOX_FILE_MISSING"

    payload = SimpleNamespace(original_name="large.zip", size_bytes=2 * 1024**3)
    with pytest.raises(ProblemException) as too_large:
        fleet.create_transfer(payload, _request(), SimpleNamespace(id="u"), _Db())
    assert too_large.value.code == "TRANSFER_FILE_TOO_LARGE"

    remote_file = SimpleNamespace(id="file", root_id="root")
    remote_root = SimpleNamespace(
        id="root", source=SimpleNamespace(value="device"), device_id="source"
    )
    denied_payload = SimpleNamespace(
        original_name="file.txt",
        size_bytes=1,
        source_file_id="file",
        source_device_id=None,
        direction="device_to_host",
        destination_device_id=None,
        destination_root_id=None,
    )
    monkeypatch.setattr(fleet, "grant_allows", lambda *_a, **_k: False)
    with pytest.raises(ProblemException) as denied:
        fleet.create_transfer(
            denied_payload,
            _request(),
            SimpleNamespace(id="u"),
            _Db(objects={"file": remote_file, "root": remote_root}),
        )
    assert denied.value.code == "GRANT_DENIED"


def test_fleet_upload_finalize_and_download_missing_or_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = SimpleNamespace(id="device")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_a, **_k: device)
    with pytest.raises(ProblemException) as upload_missing:
        asyncio.run(fleet.upload_chunk("missing", 0, _request(), "token", None, _Db()))
    assert upload_missing.value.code == "TRANSFER_NOT_FOUND"
    with pytest.raises(ProblemException) as finalize_missing:
        fleet.finalize_device_upload("missing", "token", _Db())
    assert finalize_missing.value.code == "TRANSFER_NOT_FOUND"
    with pytest.raises(ProblemException) as download_missing:
        fleet.download_chunk("missing", 0, "token", _Db())
    assert download_missing.value.code == "TRANSFER_NOT_FOUND"

    expired = SimpleNamespace(
        id="transfer",
        source_device_id="device",
        destination_device_id="device",
        status="queued",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        completed_chunks=1,
        transit_path="part",
    )
    monkeypatch.setattr(fleet, "cleanup_transfer_part", lambda *_a: None)
    db = _Db(objects={"transfer": expired})
    with pytest.raises(ProblemException) as upload_expired:
        asyncio.run(fleet.upload_chunk("transfer", 0, _request(), "token", None, db))
    assert upload_expired.value.code == "TRANSFER_EXPIRED"
    expired.status = "queued"
    expired.completed_chunks = 1
    expired.transit_path = "part"
    with pytest.raises(ProblemException) as download_expired:
        fleet.download_chunk("transfer", 0, "token", _Db(objects={"transfer": expired}))
    assert download_expired.value.code == "TRANSFER_EXPIRED"


def test_task_router_conflict_delete_and_participant_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_a: task)
    monkeypatch.setattr(tasks, "can_view_task", lambda *_a: True)
    monkeypatch.setattr(tasks, "can_manage_task", lambda *_a: True)

    with pytest.raises(ProblemException) as missing_draft:
        tasks.apply_conflict_draft("missing", _request(), None, user, _Db())
    assert missing_draft.value.code == "CONFLICT_DRAFT_NOT_FOUND"

    with pytest.raises(ProblemException) as delete_conflict:
        tasks.delete_task("task", _request(), '"2"', user, _Db())
    assert delete_conflict.value.code == "VERSION_CONFLICT"

    monkeypatch.setattr(tasks, "can_manage_task", lambda *_a: False)
    with pytest.raises(ProblemException) as add_denied:
        tasks.add_participant(
            "task", SimpleNamespace(user_id="other"), _request(), None, user, _Db()
        )
    assert add_denied.value.code == "PARTICIPANT_EDIT_DENIED"
    with pytest.raises(ProblemException) as remove_denied:
        tasks.remove_participant("task", "p1", _request(), None, user, _Db())
    assert remove_denied.value.code == "PARTICIPANT_EDIT_DENIED"

    monkeypatch.setattr(tasks, "can_manage_task", lambda *_a: True)
    with pytest.raises(ProblemException) as add_conflict:
        tasks.add_participant(
            "task", SimpleNamespace(user_id="other"), _request(), '"2"', user, _Db()
        )
    assert add_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as remove_conflict:
        tasks.remove_participant("task", "p1", _request(), '"2"', user, _Db())
    assert remove_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as participant_missing:
        tasks.remove_participant("task", "missing", _request(), None, user, _Db())
    assert participant_missing.value.code == "PARTICIPANT_NOT_FOUND"


def test_task_delete_database_cas_rejects_interleaved_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    task = _task(version=2)

    class RaceDb(_Db):
        def __init__(self) -> None:
            super().__init__()
            self.rolled_back = False

        def execute(self, *_args, **_kwargs):
            # 模拟另一进程在本进程回读之后先提交编辑；数据库 UPDATE CAS
            # 因 version 已变化返回 0 行，删除请求必须拒绝而非覆盖新内容。
            task.version = 3
            return SimpleNamespace(rowcount=0)

        def rollback(self) -> None:
            self.rolled_back = True

    db = RaceDb()
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_args: task)
    monkeypatch.setattr(tasks, "can_view_task", lambda *_args: True)
    monkeypatch.setattr(tasks, "can_manage_task", lambda *_args: True)
    with pytest.raises(ProblemException) as conflict:
        tasks.delete_task("task", _request(), '"2"', user, db)
    assert conflict.value.code == "VERSION_CONFLICT"
    assert db.rolled_back is True

    monkeypatch.setattr(tasks, "can_view_task", lambda *_args: False)
    with pytest.raises(ProblemException) as hidden:
        tasks.delete_task("task", _request(), '"3"', user, _Db())
    assert hidden.value.code == "TASK_NOT_FOUND"


def test_task_router_step_rejection_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_a: task)
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as add_denied:
        tasks.add_step("task", SimpleNamespace(), _request(), None, user, _Db())
    assert add_denied.value.code == "STEP_EDIT_DENIED"

    step = SimpleNamespace(id="step", task_id="task", version=1, title="步骤")
    with pytest.raises(ProblemException) as patch_missing:
        tasks.patch_step(
            "task", "missing", SimpleNamespace(version=None), _request(), None, user, _Db()
        )
    assert patch_missing.value.code == "STEP_NOT_FOUND"
    with pytest.raises(ProblemException) as patch_denied:
        tasks.patch_step(
            "task",
            "step",
            SimpleNamespace(version=None),
            _request(),
            None,
            user,
            _Db(objects={"step": step}),
        )
    assert patch_denied.value.code == "STEP_EDIT_DENIED"

    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: True)
    with pytest.raises(ProblemException) as version_required:
        tasks.patch_step(
            "task",
            "step",
            SimpleNamespace(version=None),
            _request(),
            None,
            user,
            _Db(objects={"step": step}),
        )
    assert version_required.value.code == "IF_MATCH_REQUIRED"
    with pytest.raises(ProblemException) as delete_missing:
        tasks.delete_step("task", "missing", _request(), None, user, _Db())
    assert delete_missing.value.code == "STEP_NOT_FOUND"
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as delete_denied:
        tasks.delete_step(
            "task", "step", _request(), None, user, _Db(objects={"step": step})
        )
    assert delete_denied.value.code == "STEP_EDIT_DENIED"
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: True)
    with pytest.raises(ProblemException) as delete_conflict:
        tasks.delete_step(
            "task", "step", _request(), '"2"', user, _Db(objects={"step": step})
        )
    assert delete_conflict.value.code == "VERSION_CONFLICT"


def test_task_router_comment_and_material_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    task = _task()
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_a: task)
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as comment_denied:
        tasks.add_comment("task", SimpleNamespace(), _request(), user, _Db())
    assert comment_denied.value.code == "COMMENT_DENIED"
    with pytest.raises(ProblemException) as material_denied:
        tasks.add_material("task", SimpleNamespace(), _request(), None, user, _Db())
    assert material_denied.value.code == "MATERIAL_EDIT_DENIED"

    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: True)
    payload = SimpleNamespace(parent_id=None, mentioned_user_ids=["missing"], body="内容")
    with pytest.raises(ProblemException) as mention_invalid:
        tasks.add_comment("task", payload, _request(), user, _Db())
    assert mention_invalid.value.code == "COMMENT_MENTION_INVALID"

    with pytest.raises(ProblemException) as material_conflict:
        tasks.add_material("task", SimpleNamespace(), _request(), '"2"', user, _Db())
    assert material_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as patch_missing:
        tasks.patch_material(
            "task", "missing", SimpleNamespace(version=None), _request(), None, user, _Db()
        )
    assert patch_missing.value.code == "MATERIAL_NOT_FOUND"
    item = SimpleNamespace(id="material", task_id="task", version=1)
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: False)
    with pytest.raises(ProblemException) as patch_denied:
        tasks.patch_material(
            "task",
            "material",
            SimpleNamespace(version=None),
            _request(),
            None,
            user,
            _Db(objects={"material": item}),
        )
    assert patch_denied.value.code == "MATERIAL_EDIT_DENIED"
    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: True)
    with pytest.raises(ProblemException) as patch_conflict:
        tasks.patch_material(
            "task",
            "material",
            SimpleNamespace(version=2),
            _request(),
            None,
            user,
            _Db(objects={"material": item}),
        )
    assert patch_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as upload_missing:
        asyncio.run(
            tasks.upload_material_version(
                "task",
                "missing",
                _request(),
                None,
                SimpleNamespace(),
                "draft",
                False,
                "",
                user,
                _Db(),
            )
        )
    assert upload_missing.value.code == "MATERIAL_NOT_FOUND"


def test_task_router_attachment_missing_record_or_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id="user")
    with pytest.raises(ProblemException) as record_missing:
        tasks.download_attachment("missing", _request(), user, _Db(execute_values=[[]]))
    assert record_missing.value.code == "ATTACHMENT_NOT_FOUND"

    version = SimpleNamespace(id="version", original_name="附件.txt")
    blob = SimpleNamespace(relative_path="missing/blob")
    material = SimpleNamespace(task_id="task")
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_a: _task())
    monkeypatch.setattr(tasks, "resolve_blob_path", lambda _path: tmp_path / "missing")
    with pytest.raises(ProblemException) as file_missing:
        tasks.download_attachment(
            "version",
            _request(),
            user,
            _Db(execute_values=[[(version, blob, material)]]),
        )
    assert file_missing.value.code == "ATTACHMENT_MISSING"


def test_productivity_saved_topic_automation_and_calendar_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    view = SimpleNamespace(id="view", owner_id="user", version=2)
    with pytest.raises(ProblemException) as view_missing:
        productivity.delete_saved_view("missing", '"1"', user, _Db())
    assert view_missing.value.code == "SAVED_VIEW_NOT_FOUND"
    with pytest.raises(ProblemException) as view_conflict:
        productivity.delete_saved_view(
            "view", '"1"', user, _Db(objects={"view": view})
        )
    assert view_conflict.value.code == "VERSION_CONFLICT"

    with pytest.raises(ProblemException) as topic_duplicate:
        productivity.create_topic(
            SimpleNamespace(name="专题", description=""),
            user,
            _Db(scalar_values=[SimpleNamespace(id="existing")]),
        )
    assert topic_duplicate.value.code == "TOPIC_EXISTS"

    rule = SimpleNamespace(id="rule", owner_id="user", version=2)
    for operation, args in (
        (
            productivity.patch_automation_rule,
            ("missing", SimpleNamespace(), '"1"', user, _Db()),
        ),
        (
            productivity.delete_automation_rule,
            ("missing", '"1"', user, _Db()),
        ),
    ):
        with pytest.raises(ProblemException) as missing:
            operation(*args)
        assert missing.value.code == "AUTOMATION_RULE_NOT_FOUND"
    for operation, args in (
        (
            productivity.patch_automation_rule,
            ("rule", SimpleNamespace(), '"1"', user, _Db(objects={"rule": rule})),
        ),
        (
            productivity.delete_automation_rule,
            ("rule", '"1"', user, _Db(objects={"rule": rule})),
        ),
    ):
        with pytest.raises(ProblemException) as conflict:
            operation(*args)
        assert conflict.value.code == "VERSION_CONFLICT"

    assert productivity.list_calendar(None, user, _Db(scalars_values=[[]])) == []
    assert productivity.list_calendar(2026, user, _Db(scalars_values=[[]])) == []
    with pytest.raises(ProblemException) as calendar_duplicate:
        productivity.create_calendar_entry(
            SimpleNamespace(date_key="2026-10-01", kind="holiday"),
            user,
            _Db(scalar_values=[SimpleNamespace(id="existing")]),
        )
    assert calendar_duplicate.value.code == "WORK_CALENDAR_ENTRY_EXISTS"

    entry = SimpleNamespace(id="entry", version=2)
    for operation, args in (
        (
            productivity.patch_calendar_entry,
            ("missing", SimpleNamespace(), '"1"', user, _Db()),
        ),
        (
            productivity.delete_calendar_entry,
            ("missing", '"1"', user, _Db()),
        ),
    ):
        with pytest.raises(ProblemException) as missing:
            operation(*args)
        assert missing.value.code == "WORK_CALENDAR_ENTRY_NOT_FOUND"
    for operation, args in (
        (
            productivity.patch_calendar_entry,
            ("entry", SimpleNamespace(), '"1"', user, _Db(objects={"entry": entry})),
        ),
        (
            productivity.delete_calendar_entry,
            ("entry", '"1"', user, _Db(objects={"entry": entry})),
        ),
    ):
        with pytest.raises(ProblemException) as conflict:
            operation(*args)
        assert conflict.value.code == "VERSION_CONFLICT"


def test_productivity_text_compare_and_handover_file_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    left = SimpleNamespace(extracted_text="", ocr_text="")
    right = SimpleNamespace(extracted_text="正文", ocr_text="")
    with pytest.raises(ProblemException) as unavailable:
        productivity.compare_documents(
            SimpleNamespace(left_file_id="left", right_file_id="right", comparison_type="text"),
            SimpleNamespace(id="u"),
            _Db(objects={"left": left, "right": right}),
        )
    assert unavailable.value.code == "TEXT_COMPARE_UNAVAILABLE"

    record = SimpleNamespace(filename="missing.zip", created_by="u")
    monkeypatch.setattr(
        productivity,
        "get_settings",
        lambda: SimpleNamespace(exports_dir=tmp_path),
    )
    with pytest.raises(ProblemException) as missing:
        productivity.download_handover(
            "record",
            SimpleNamespace(id="u", role=UserRole.STAFF),
            _Db(objects={"record": record}),
        )
    assert missing.value.code == "HANDOVER_FILE_MISSING"


def test_workspace_sharing_and_member_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    host = SimpleNamespace(id="root", source=SimpleNamespace(value="host"), version=1)
    device_root = SimpleNamespace(
        id="root",
        source=SimpleNamespace(value="device"),
        version=2,
        semantic_content_enabled=True,
    )
    monkeypatch.setattr(workspace_router, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: host)
    with pytest.raises(ProblemException) as host_fixed:
        workspace_router.patch_workspace_root_sharing(
            "root", SimpleNamespace(), _request(), '"1"', user, _Db()
        )
    assert host_fixed.value.code == "HOST_ROOT_SHARING_FIXED"

    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: device_root)
    with pytest.raises(ProblemException) as sharing_conflict:
        workspace_router.patch_workspace_root_sharing(
            "root", SimpleNamespace(), _request(), '"1"', user, _Db()
        )
    assert sharing_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as members_conflict:
        workspace_router.replace_workspace_root_members(
            "root", SimpleNamespace(members=[]), _request(), '"1"', user, _Db()
        )
    assert members_conflict.value.code == "VERSION_CONFLICT"

    duplicate = SimpleNamespace(user_id="u1")
    with pytest.raises(ProblemException) as duplicate_member:
        workspace_router.replace_workspace_root_members(
            "root",
            SimpleNamespace(members=[duplicate, duplicate]),
            _request(),
            '"2"',
            user,
            _Db(),
        )
    assert duplicate_member.value.code == "DUPLICATE_ROOT_MEMBER"
    with pytest.raises(ProblemException) as invalid_member:
        workspace_router.replace_workspace_root_members(
            "root",
            SimpleNamespace(members=[SimpleNamespace(user_id="missing")]),
            _request(),
            '"2"',
            user,
            _Db(scalars_values=[[]]),
        )
    assert invalid_member.value.code == "ROOT_MEMBER_INVALID"


def test_workspace_root_scan_and_patch_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    host = SimpleNamespace(id="root", source=SimpleNamespace(value="host"), enabled=True, version=2)
    with pytest.raises(ProblemException) as folder_missing:
        workspace_router.list_workspace_folder_options("missing", user, _Db())
    assert folder_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as selection_missing:
        workspace_router.patch_workspace_selection(
            "missing", SimpleNamespace(), _request(), SimpleNamespace(), '"1"', user, _Db()
        )
    assert selection_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as selection_conflict:
        workspace_router.patch_workspace_selection(
            "root", SimpleNamespace(), _request(), SimpleNamespace(), '"1"', user, _Db(objects={"root": host})
        )
    assert selection_conflict.value.code == "VERSION_CONFLICT"

    with pytest.raises(ProblemException) as patch_missing:
        workspace_router.patch_workspace_root(
            "missing", SimpleNamespace(), _request(), '"1"', user, _Db()
        )
    assert patch_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as patch_conflict:
        workspace_router.patch_workspace_root(
            "root", SimpleNamespace(), _request(), '"1"', user, _Db(objects={"root": host})
        )
    assert patch_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as delete_missing:
        workspace_router.delete_workspace_root("missing", _request(), '"1"', user, _Db())
    assert delete_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as delete_conflict:
        workspace_router.delete_workspace_root(
            "root", _request(), '"1"', user, _Db(objects={"root": host})
        )
    assert delete_conflict.value.code == "VERSION_CONFLICT"

    with pytest.raises(ProblemException) as scan_missing:
        workspace_router.start_workspace_scan("missing", _request(), user, _Db())
    assert scan_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as scan_running:
        workspace_router.start_workspace_scan(
            "root",
            _request(),
            user,
            _Db(objects={"root": host}, scalar_values=[SimpleNamespace(id="job")]),
        )
    assert scan_running.value.code == "SCAN_ALREADY_RUNNING"
    with pytest.raises(ProblemException) as sync_missing:
        workspace_router.scan_workspace_now("missing", _request(), user, _Db())
    assert sync_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"


def test_workspace_access_open_link_and_unlink_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    monkeypatch.setattr(workspace_router, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(
        workspace_router,
        "workspace_root_permissions",
        lambda *_a: {"browse": False},
    )
    with pytest.raises(ProblemException) as denied:
        workspace_router.list_workspace_files(
            _request(), "missing", None, False, 50, user, _Db()
        )
    assert denied.value.code == "WORKSPACE_ACCESS_DENIED"

    item = SimpleNamespace(id="file", version=2, is_directory=False)
    monkeypatch.setattr(workspace_router, "get_file", lambda *_a, **_k: (item, None))
    with pytest.raises(ProblemException) as link_conflict:
        workspace_router.link_workspace_file(
            "file", SimpleNamespace(), _request(), '"1"', user, _Db()
        )
    assert link_conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as unlink_conflict:
        workspace_router.unlink_workspace_file(
            "file", "link", _request(), '"1"', user, _Db()
        )
    assert unlink_conflict.value.code == "VERSION_CONFLICT"
    item.version = 1
    with pytest.raises(ProblemException) as link_missing:
        workspace_router.unlink_workspace_file(
            "file", "missing", _request(), '"1"', user, _Db()
        )
    assert link_missing.value.code == "WORKSPACE_LINK_NOT_FOUND"


def test_workspace_sharing_members_tags_and_new_link_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    root = SimpleNamespace(
        id="root",
        source=SimpleNamespace(value="device"),
        version=2,
        semantic_content_enabled=True,
        share_scope="team",
    )
    monkeypatch.setattr(workspace_router, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: root)
    monkeypatch.setattr(workspace_router, "root_to_out", lambda *_a: root)
    monkeypatch.setattr(workspace_router, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(workspace_router, "emit_event", lambda *_a, **_k: None)
    checkpoint = SimpleNamespace(id="checkpoint")
    payload = SimpleNamespace(share_scope="selected", semantic_content_enabled=False)
    db = _Db(scalars_values=[["file"], [checkpoint]])
    assert workspace_router.patch_workspace_root_sharing(
        "root", payload, _request(), '"2"', user, db
    ) is root
    assert checkpoint in db.deleted

    host = SimpleNamespace(source=SimpleNamespace(value="host"), version=1)
    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: host)
    with pytest.raises(ProblemException) as unsupported:
        workspace_router.replace_workspace_root_members(
            "root", SimpleNamespace(members=[]), _request(), '"1"', user, _Db()
        )
    assert unsupported.value.code == "ROOT_MEMBERS_UNSUPPORTED"

    root.version = 1
    monkeypatch.setattr(workspace_router, "require_root_manager", lambda *_a: root)
    existing = SimpleNamespace(
        user_id="u1",
        active=True,
        version=1,
        can_browse=False,
        can_download=False,
        can_send=False,
    )
    member1 = SimpleNamespace(
        user_id="u1", can_browse=True, can_download=True, can_send=False
    )
    member2 = SimpleNamespace(
        user_id="u2", can_browse=True, can_download=False, can_send=True
    )
    members_db = _Db(scalars_values=[["u1", "u2"], [existing], [existing]])
    result = workspace_router.replace_workspace_root_members(
        "root",
        SimpleNamespace(members=[member1, member2]),
        _request(),
        '"1"',
        user,
        members_db,
    )
    assert result == [existing] and existing.active and len(members_db.added) == 1

    file_item = SimpleNamespace(id="file", version=1, name="文件")
    old_tag = SimpleNamespace(tag="旧")
    monkeypatch.setattr(workspace_router, "get_file", lambda *_a, **_k: (file_item, root))
    monkeypatch.setattr(workspace_router, "workspace_file_out", lambda *_a, **_k: file_item)
    tag_db = _Db(scalars_values=[[old_tag]])
    workspace_router.patch_workspace_tags(
        "file", SimpleNamespace(tags=["新"]), _request(), '"1"', user, tag_db
    )
    assert old_tag in tag_db.deleted and file_item.version == 2

    file_item.version = 1
    link_payload = SimpleNamespace(
        entity_type="report",
        entity_id="report",
        relation="supports",
        model_dump=lambda: {
            "entity_type": "report",
            "entity_id": "report",
            "relation": "supports",
        },
    )
    monkeypatch.setattr(workspace_router, "validate_link_target", lambda *_a: None)
    monkeypatch.setattr(workspace_router, "record_system_entry", lambda *_a, **_k: None)
    link_db = _Db(scalar_values=[None])
    workspace_router.link_workspace_file(
        "file", link_payload, _request(), '"1"', user, link_db
    )
    assert file_item.version == 2 and link_db.added


def test_workspace_create_duplicate_list_and_local_open_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.ADMIN)
    root_path = tmp_path / "root"
    root_path.mkdir()
    monkeypatch.setattr(workspace_router, "validate_root_path", lambda _path: root_path)
    with pytest.raises(ProblemException) as duplicate:
        workspace_router.create_workspace_root(
            SimpleNamespace(absolute_path=str(root_path), name="目录"),
            _request(),
            SimpleNamespace(),
            user,
            _Db(scalar_values=[None, "name-in-use"]),
        )
    assert duplicate.value.code == "WORKSPACE_ROOT_EXISTS"

    root = SimpleNamespace(id="root", enabled=True, source=SimpleNamespace(value="host"))
    monkeypatch.setattr(
        workspace_router,
        "workspace_root_permissions",
        lambda *_a: {"browse": True},
    )
    assert workspace_router.list_workspace_files(
        _request(), "root", None, False, 50, user, _Db(objects={"root": root}, scalars_values=[[]])
    ) == []

    item = SimpleNamespace(
        id="file",
        root_id="root",
        is_directory=True,
        in_scope=True,
    )
    monkeypatch.setattr(workspace_router, "is_host_local_request", lambda _request: True)
    grant = SimpleNamespace(revoked_at=None, used_at=None, expires_at=datetime.now(timezone.utc) + timedelta(minutes=1), file_id="file")
    with pytest.raises(ProblemException) as denied:
        workspace_router.resolve_local_open_token(
            "valid_token", _request(), _Db(objects={"file": item, "root": root}, scalar_values=[grant])
        )
    assert denied.value.code == "LOCAL_OPEN_DENIED"


def test_tasks_remaining_conflict_new_participant_and_step_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user", role=UserRole.STAFF)
    draft = SimpleNamespace(id="draft", task_id="missing")
    assert tasks.list_conflicts(user, _Db(scalars_values=[[draft]])) == []

    task = _task()
    participant_user = SimpleNamespace(id="other", display_name="其他成员")
    monkeypatch.setattr(tasks, "get_task_or_404", lambda *_a: task)
    monkeypatch.setattr(tasks, "can_manage_task", lambda *_a: True)
    monkeypatch.setattr(tasks, "record_system_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "emit_event", lambda *_a, **_k: None)
    monkeypatch.setattr(tasks, "task_to_out", lambda *_a, **_k: task)
    db = _Db(objects={"other": participant_user}, scalar_values=[None])
    assert tasks.add_participant(
        "task",
        schemas.ParticipantAdd(user_id="other"),
        _request(),
        None,
        user,
        db,
    ) is task
    assert db.added

    monkeypatch.setattr(tasks, "can_edit_task", lambda *_a: True)
    with pytest.raises(ProblemException) as conflict:
        tasks.add_step(
            "task", SimpleNamespace(), _request(), '"1"', user, _Db()
        )
    assert conflict.value.code == "VERSION_CONFLICT"


def test_task_service_valid_update_dashboard_preferences_and_status_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = SimpleNamespace(id="owner", role=UserRole.ADMIN)
    task = _task(reviewer_id="reviewer")
    monkeypatch.setattr(task_service, "can_edit_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "can_manage_task", lambda *_a: True)
    monkeypatch.setattr(task_service, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "record_system_entry", lambda *_a, **_k: None)
    monkeypatch.setattr(task_service, "emit_event", lambda *_a, **_k: None)
    active = SimpleNamespace(active=True)
    db = _Db(
        objects={"owner2": active, "reviewer": active},
        scalars_values=[["c1"]],
        execute_values=[SimpleNamespace(rowcount=1)],
    )
    payload = schemas.TaskUpdate(
        owner_id="owner2", reviewer_id="reviewer", contact_ids=["c1"]
    )
    assert task_service.update_task(db, task, payload, 1, actor) is task

    monkeypatch.setattr(task_service, "visible_tasks", lambda *_a: [])
    monkeypatch.setattr(task_service, "task_to_out", lambda *_a, **_k: None)
    disabled = SimpleNamespace(enabled=False, advance_days=3)
    task_service.dashboard(_Db(objects={"owner": disabled}, scalar_values=[0]), actor)
    enabled = SimpleNamespace(
        enabled=True,
        advance_days=3,
        remind_overdue=False,
        remind_review=False,
        remind_feedback=False,
        remind_materials=False,
    )
    task_service.dashboard(_Db(objects={"owner": enabled}, scalar_values=[0]), actor)

    visible = [_task(status=TaskStatus.COMPLETED), _task(id="other", status=TaskStatus.IN_PROGRESS)]
    monkeypatch.setattr(task_service, "visible_tasks", lambda *_a: visible)
    result = task_service.list_tasks(_Db(), actor, status=TaskStatus.COMPLETED)
    assert len(result) == 1 and result[0].status == TaskStatus.COMPLETED


def test_productivity_global_search_stage_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(productivity, "semantic_rerank_search_items", lambda _db, _q, items: items)
    monkeypatch.setattr(productivity, "request_device", lambda *_a: None)
    user = SimpleNamespace(id="user", role=SimpleNamespace(value="staff"))
    now = datetime.now(timezone.utc)
    cases = [
        [SimpleNamespace(id="task", title="事项", category="", status=SimpleNamespace(value="in_progress"), updated_at=now)],
        [],
    ]
    assert productivity.global_search(_request(), "", 1, user, _Db(scalars_values=[cases[0]]))["total"] == 1

    contact = SimpleNamespace(id="contact", name="联系人", organization="", note="")
    journal = SimpleNamespace(id="journal", title="日志", content="", task_id=None, immutable=False, updated_at=now)
    report = SimpleNamespace(id="report", title="报告", summary="", period_key="2026-08", status=SimpleNamespace(value="draft"), updated_at=now)
    knowledge = SimpleNamespace(id="knowledge", title="知识", category="", body="", updated_at=now)
    for sequence in (
        [[], [contact]],
        [[], [], [journal]],
        [[], [], [], [report]],
        [[], [], [], [], [knowledge]],
    ):
        assert productivity.global_search(_request(), "", 1, user, _Db(scalars_values=sequence))["total"] == 1

    admin = SimpleNamespace(id="admin", role=SimpleNamespace(value="admin"))
    device = SimpleNamespace(
        id="device",
        name="协同机",
        platform="windows",
        architecture="amd64",
        local_username="user",
        status=SimpleNamespace(value="online"),
        last_seen_at=None,
    )
    assert productivity.global_search(
        _request(), "", 1, admin, _Db(scalars_values=[[], [], [], [], [], [device]])
    )["total"] == 1
