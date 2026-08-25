"""rc.3 最终发布门禁的防御分支回归。

这些用例专门覆盖正常界面难以触达的拒绝、回滚和兼容路径，避免为了
覆盖率数字删除生产保护条件。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import urllib.error
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app import (
    ai_service,
    appearance,
    calendar_service,
    client_agent,
    official_format_service,
    schemas,
    security,
    setup_wizard,
    update_executor,
    upgrades,
    versioning,
    work_journal,
    workspace_access,
)
from app.enums import (
    ArtLevel,
    CalendarEventType,
    FileIndexStatus,
    RecurrenceExceptionAction,
    SeasonTheme,
    Sensitivity,
    UpdateStatus,
    UserRole,
)
from app.models import (
    Device,
    Task,
    UpdatePackage,
    UpdateRun,
    WorkspaceFile,
    WorkspaceRoot,
)
from app.problems import ProblemException
from app.routers import recurrence_extensions, router_utils


class _LookupDb:
    def __init__(self, objects=None) -> None:
        self.objects = objects or {}
        self.added: list[object] = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalar(self, _statement):
        return None

    def add(self, value) -> None:
        self.added.append(value)


def test_schema_normalizers_cover_none_empty_invalid_and_deduplicated_values() -> None:
    assert schemas.MaterialInput(category="  类别  ", name=" 材料 ").category == "类别"
    with pytest.raises(ValidationError, match="材料类别和名称不能为空"):
        schemas.MaterialInput(category="   ", name="材料")

    assert schemas.TaskUpdate(tags=None).tags is None
    assert schemas.TaskUpdate(tags=[" 甲 ", "", "甲", "乙"]).tags == ["甲", "乙"]
    assert schemas.ReminderPreferencePatch(reminder_days=None).reminder_days is None
    assert schemas.ReminderPreferencePatch(reminder_days=[1, 3, 1]).reminder_days == [
        3,
        1,
    ]
    with pytest.raises(ValidationError, match="0—30"):
        schemas.ReminderPreferencePatch(reminder_days=[31])

    calculated = schemas.PartyDevelopmentCalculateRequest(
        name=" 张   三 ", application_date=date(2026, 1, 1)
    )
    assert calculated.name == "张 三"
    with pytest.raises(ValidationError, match="姓名不能为空"):
        schemas.PartyDevelopmentCalculateRequest(
            name="   ", application_date=date(2026, 1, 1)
        )


def test_appearance_invalid_global_values_and_every_effective_theme_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert appearance.automatic_season() in set(SeasonTheme)
    setting = SimpleNamespace(
        value={
            "theme_mode": "invalid",
            "fixed_theme": "invalid",
            "default_art_level": "invalid",
            "version": 0,
        }
    )
    config = appearance.global_appearance(
        _LookupDb({appearance.GLOBAL_APPEARANCE_KEY: setting})
    )
    assert config == {
        "theme_mode": "auto",
        "fixed_theme": SeasonTheme.SPRING.value,
        "default_art_level": ArtLevel.STANDARD.value,
        "default_reduce_motion": False,
        "version": 1,
    }
    preference = SimpleNamespace(theme_override=SeasonTheme.WINTER)
    assert appearance.effective_season(config, preference) == SeasonTheme.WINTER.value
    assert (
        appearance.effective_season({**config, "theme_mode": "fixed"})
        == SeasonTheme.SPRING.value
    )
    monkeypatch.setattr(appearance, "automatic_season", lambda: SeasonTheme.AUTUMN)
    assert appearance.effective_season(config) == SeasonTheme.AUTUMN.value


@pytest.mark.parametrize(
    ("title", "content", "expected"),
    [
        ("新建事项：测试", "", "task.created"),
        ("更新事项：测试", "", "task.updated"),
        ("状态变更：测试", "待接收→办理中；备注", "task.status_changed"),
        ("上传材料：测试", "", "material.uploaded"),
        ("关联原始文件：测试", "", "workspace.file_linked"),
        ("固化归档：测试", "", "workspace.file_frozen"),
        ("其他事件", "", ""),
    ],
)
def test_legacy_work_journal_title_matrix(
    title: str, content: str, expected: str
) -> None:
    event, _payload = work_journal._legacy_event(
        SimpleNamespace(event_code="", event_data={}, title=title, content=content)
    )
    assert event == expected


@pytest.mark.parametrize(
    "target",
    ["task", "file", "report", "none"],
)
def test_system_journal_creates_activity_only_for_scoped_objects(target: str) -> None:
    db = _LookupDb()
    kwargs = {
        "task_id": "task-1" if target == "task" else None,
        "file_id": "file-1" if target == "file" else None,
        "report_id": "report-1" if target == "report" else None,
    }
    work_journal.record_system_entry(
        db,
        SimpleNamespace(id="actor"),
        "发布门禁",
        event_code="release.checked",
        **kwargs,
    )
    assert len(db.added) == (1 if target == "none" else 2)


def test_ai_provider_resolution_covers_public_private_empty_and_mixed_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        ai_service.validate_provider_url("https://1.1.1.1/v1", False, resolve=False)
        is False
    )
    assert (
        ai_service.validate_provider_url(
            "http://localhost:9000/v1", True, resolve=False
        )
        is True
    )

    monkeypatch.setattr(ai_service.socket, "getaddrinfo", lambda *_a, **_k: [])
    assert (
        ai_service.validate_provider_url(
            "https://empty.example/v1", False, resolve=True
        )
        is False
    )

    monkeypatch.setattr(
        ai_service.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (None, None, None, None, ("10.0.0.8", 443)),
            (None, None, None, None, ("1.1.1.1", 443)),
        ],
    )
    with pytest.raises(ProblemException) as mixed:
        ai_service.validate_provider_url("https://mixed.example/v1", True, resolve=True)
    assert mixed.value.code == "AI_ENDPOINT_FORBIDDEN"


def _ai_task(identity: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=identity,
        title=f"事项 {identity}",
        description="发布检查",
        category="发布",
        experience_notes="",
        sensitivity=Sensitivity.NORMAL,
    )


def test_ai_source_scope_and_automatic_collection_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id="user")
    policy = SimpleNamespace(
        allowed_task_categories=["发布"],
        allowed_root_ids=["root-1"],
        allowed_file_types=[],
    )
    task = _ai_task("task-1")
    db = _LookupDb({(Task, task.id): task})

    monkeypatch.setattr(ai_service, "can_view_task", lambda *_a: False)
    with pytest.raises(ProblemException) as denied:
        ai_service._task_source(db, task, user, policy)
    assert denied.value.code == "TASK_NOT_FOUND"
    monkeypatch.setattr(ai_service, "can_view_task", lambda *_a: True)
    task.category = "未授权"
    with pytest.raises(ProblemException) as scoped:
        ai_service._task_source(db, task, user, policy)
    assert scoped.value.code == "AI_TASK_SCOPE_DENIED"

    tasks = [_ai_task(f"task-{index}") for index in range(6)]
    root = SimpleNamespace(id="root-1", enabled=True, name="资料")
    files = [
        SimpleNamespace(
            id=f"file-{index}",
            root_id="other" if index == 0 else "root-1",
            status=FileIndexStatus.INDEXED,
            extension=".txt",
            extracted_text="正文",
            ocr_text="",
            name=f"文件 {index}",
        )
        for index in range(5)
    ]
    objects = {(WorkspaceRoot, "root-1"): root}
    objects.update({(WorkspaceFile, item.id): item for item in files})
    db = _LookupDb(objects)
    monkeypatch.setattr(ai_service, "visible_tasks", lambda *_a: tasks)
    monkeypatch.setattr(ai_service, "search_workspace_files", lambda *_a, **_k: files)
    sources, excerpts = ai_service.collect_sources(db, user, policy, "", [], [])
    assert len(sources) == len(excerpts) == 8

    policy.allowed_root_ids = []
    monkeypatch.setattr(ai_service, "visible_tasks", lambda *_a: [])
    assert ai_service.collect_sources(db, user, policy, "不匹配", [], []) == ([], [])


class _QuickCheck:
    def __init__(self, result: str) -> None:
        self.result = result

    def execute(self, _query: str):
        return SimpleNamespace(fetchone=lambda: (self.result,))

    def close(self) -> None:
        return None


def _backup_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("database/partyops.db", b"sqlite")


def test_upgrade_restore_rejects_bad_extracted_and_restored_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.zip"
    _backup_zip(backup)
    settings = SimpleNamespace(
        data_dir=tmp_path, database_path=tmp_path / "partyops.db"
    )
    monkeypatch.setattr(upgrades, "get_settings", lambda: settings)
    monkeypatch.setattr(upgrades, "verify_backup", lambda _path: None)
    monkeypatch.setattr(upgrades.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(upgrades.db_runtime, "rebuild", lambda: None)

    monkeypatch.setattr(
        upgrades, "sqlite3_connect", lambda _path: _QuickCheck("broken")
    )
    with pytest.raises(RuntimeError, match="升级前备份数据库完整性"):
        upgrades.restore_database_from_upgrade_backup(backup)

    checks = iter([_QuickCheck("ok"), _QuickCheck("broken")])
    monkeypatch.setattr(upgrades, "sqlite3_connect", lambda _path: next(checks))
    with pytest.raises(RuntimeError, match="恢复后的数据库完整性"):
        upgrades.restore_database_from_upgrade_backup(backup)


def test_upgrade_restore_reinstates_previous_database_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup = tmp_path / "backup.zip"
    _backup_zip(backup)
    database = tmp_path / "partyops.db"
    database.write_bytes(b"old")
    settings = SimpleNamespace(data_dir=tmp_path, database_path=database)
    monkeypatch.setattr(upgrades, "get_settings", lambda: settings)
    monkeypatch.setattr(upgrades, "verify_backup", lambda _path: None)
    monkeypatch.setattr(upgrades.db_runtime, "dispose", lambda: None)
    monkeypatch.setattr(upgrades, "sqlite3_connect", lambda _path: _QuickCheck("ok"))
    real_replace = os.replace
    calls = 0

    def fail_new_source(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated install failure")
        return real_replace(source, destination)

    monkeypatch.setattr(upgrades.os, "replace", fail_new_source)
    with pytest.raises(OSError, match="simulated"):
        upgrades.restore_database_from_upgrade_backup(backup)
    assert database.read_bytes() == b"old"


def test_update_lock_write_failure_and_managed_tree_link_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    update_executor._assert_managed_tree_has_no_links(missing)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="链接"):
        update_executor._assert_managed_tree_has_no_links(link)

    lock = tmp_path / "locks" / "update.lock"
    monkeypatch.setattr(
        update_executor.os,
        "write",
        lambda *_a: (_ for _ in ()).throw(OSError("disk full")),
    )
    assert not update_executor._acquire_update_lock(lock)
    assert not lock.exists()


def test_secure_update_transaction_uses_protected_windows_location_and_rejects_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(update_executor, "_getenv", lambda _key: "production")
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "program-data"))
    transaction = update_executor._secure_update_backup_root("run-windows-1")
    assert (
        transaction.parent
        == tmp_path / "program-data" / "PartyOps-System" / "update-transactions"
    )
    with pytest.raises(RuntimeError, match="已存在"):
        update_executor._secure_update_backup_root("run-windows-1")


def test_installed_deb_snapshot_copies_regular_symlink_and_control_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "program" / "partyops"
    source.parent.mkdir()
    source.write_bytes(b"runtime")
    directory = tmp_path / "directory"
    directory.mkdir()
    symlink = tmp_path / "runtime-link"
    symlink.symlink_to(source)
    destination = tmp_path / "rollback" / "partyops.deb"
    listed = "\n".join(
        [
            "relative",
            str(tmp_path / "missing"),
            str(directory),
            str(source),
            str(symlink),
        ]
    )
    copied: list[str] = []
    original_is_file = Path.is_file
    original_relative_to = Path.relative_to
    original_copy2 = update_executor.shutil.copy2

    def is_file(path: Path) -> bool:
        return path.name.startswith("partyops.") or original_is_file(path)

    def copy2(source_path, target_path, **kwargs):
        copied.append(Path(source_path).name)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(source_path).exists():
            return original_copy2(source_path, target_path, **kwargs)
        target.write_bytes(b"maintainer-script")
        return target

    def relative_to(path: Path, other, *args, **kwargs):
        # 该函数只在 Linux 更新器运行；Windows 测试机把盘符后的部分映射成
        # Linux 根路径下的相对成员，以验证相同的归档复制分支。
        if str(other) in {"/", "\\"}:
            return Path(*path.parts[1:])
        return original_relative_to(path, other, *args, **kwargs)

    def run(command, **_kwargs):
        if command[:3] == ["dpkg-query", "-L", "partyops"]:
            return subprocess.CompletedProcess(command, 0, listed, "")
        if command[:2] == ["dpkg-deb", "--build"]:
            destination.write_bytes(b"deb")
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    monkeypatch.setattr(
        update_executor, "_installed_package_version", lambda: "1.4.3~rc3"
    )
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "relative_to", relative_to)
    monkeypatch.setattr(update_executor.shutil, "copy2", copy2)
    monkeypatch.setattr(update_executor, "_run", run)
    update_executor._create_installed_package_snapshot(destination)
    assert destination.is_file()
    assert "partyops" in copied and "partyops.preinst" in copied


def test_installed_deb_snapshot_rejects_build_without_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        update_executor, "_installed_package_version", lambda: "1.4.3~rc3"
    )
    monkeypatch.setattr(update_executor, "_architecture", lambda: "arm64")
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "" if command[0] == "dpkg-query" else "", ""
        ),
    )
    with pytest.raises(RuntimeError, match="回滚包"):
        update_executor._create_installed_package_snapshot(tmp_path / "missing.deb")


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("already-stopped", (True, False)),
        ("helper-ok", (True, True)),
        ("helper-failed", (False, False)),
        ("sc-not-running", (True, False)),
        ("sc-denied", (False, False)),
        ("test-stop", (True, True)),
        ("poll-stopped", (True, True)),
        ("poll-timeout", (False, True)),
    ],
)
def test_windows_service_stop_state_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
    expected: tuple[bool, bool],
) -> None:
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"runtime")
    helper = tmp_path / "PartyOpsService.exe"
    if scenario.startswith("helper"):
        helper.write_bytes(b"helper")
    monkeypatch.setattr(update_executor.sys, "executable", str(executable))
    monkeypatch.setenv(
        "PARTYOPS_ENVIRONMENT",
        "test" if scenario == "test-stop" else "production",
    )
    query_calls = 0

    def run(command, **_kwargs):
        nonlocal query_calls
        if command[:2] == ["sc.exe", "query"]:
            query_calls += 1
            stopped = scenario == "already-stopped" or (
                scenario == "poll-stopped" and query_calls > 1
            )
            output = "STATE : 1 STOPPED" if stopped else "STATE : 4 RUNNING"
            return subprocess.CompletedProcess(command, 0, output, "")
        if command[0] == str(helper):
            code = 0 if scenario == "helper-ok" else 1
            return subprocess.CompletedProcess(command, code, "", "")
        code = (
            1062
            if scenario == "sc-not-running"
            else 5
            if scenario == "sc-denied"
            else 0
        )
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(update_executor, "_run", run)
    monkeypatch.setattr(update_executor.time, "sleep", lambda _seconds: None)
    if scenario == "poll-timeout":
        times = iter([0.0, 61.0])
        monkeypatch.setattr(update_executor.time, "monotonic", lambda: next(times))
    elif scenario == "poll-stopped":
        monkeypatch.setattr(update_executor.time, "monotonic", lambda: 0.0)
    assert update_executor._stop_windows_host_service() == expected


def test_update_health_rejects_empty_reported_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        bind_host="127.0.0.1",
        port=18765,
        tls_enabled=False,
        mode="host",
    )
    payload = b'{"status":"ok","mode":"host","app_version":"","sqlite":{"safe_version":true,"fts5":true}}'

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return payload

    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.urllib.request, "urlopen", lambda *_a, **_k: Response()
    )
    assert not update_executor._health_check("1.4.3-rc.3")


def test_update_downgrade_bridge_and_daemon_retry_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(app_version="1.4.3-rc.1"),
    )
    with pytest.raises(RuntimeError, match="UPDATE_BRIDGE_REQUIRED"):
        update_executor._assert_update_not_downgrade(
            {"version": "1.4.3-rc.3", "min_version": "1.4.3-rc.2"}
        )

    attempts = 0

    class BrokenFactory:
        def __enter__(self):
            nonlocal attempts
            attempts += 1
            raise OperationalError("SELECT", {}, RuntimeError("busy"))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: BrokenFactory()
    )

    def stop_daemon(_seconds: float) -> None:
        raise StopIteration

    monkeypatch.setattr(update_executor.time, "sleep", stop_daemon)
    with pytest.raises(StopIteration):
        update_executor.run_daemon(once=False)
    assert attempts == 1


def test_trusted_host_environment_rejects_unsafe_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        update_executor, "_trusted_system_environment_file", lambda _path: False
    )
    assert update_executor._candidate_host_environments() == []


def test_execute_host_update_missing_run_and_package_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(updates_dir=tmp_path, data_dir=tmp_path)

    class Session:
        def __init__(self, run=None, package=None) -> None:
            self.run = run
            self.package = package

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, _identity):
            return (
                self.run
                if model is UpdateRun
                else self.package
                if model is UpdatePackage
                else None
            )

    current = Session()
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: current)
    assert not update_executor.execute_host_update("missing")

    current.run = SimpleNamespace(id="run", package_id="package", target_device_id=None)
    assert not update_executor.execute_host_update("run")
    current.package = SimpleNamespace(
        id="package",
        filename="release.partyops-update",
        sha256="0" * 64,
        status=UpdateStatus.COMPLETED,
    )
    assert not update_executor.execute_host_update("run")


def _os_proxy(name: str, **overrides) -> SimpleNamespace:
    proxy = SimpleNamespace(**vars(os))
    proxy.name = name
    for key, value in overrides.items():
        setattr(proxy, key, value)
    return proxy


class _WizardHealthResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_wizard_health_terminal_invalid_personal_and_timeout_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    terminal_statuses = [
        {"updated_at": "old", "code": "CHILD_EXITED", "detail": "旧诊断"}
    ]
    monkeypatch.setattr(
        setup_wizard,
        "read_service_status",
        lambda _path: (
            terminal_statuses.pop(0)
            if terminal_statuses
            else {
                "updated_at": "current",
                "code": "CHILD_EXITED",
                "detail": "子进程退出",
            }
        ),
    )
    with pytest.raises(setup_wizard.HostStartupError) as terminal:
        setup_wizard.wait_for_host_health(
            "192.168.1.8", 18765, data_dir=tmp_path, timeout=5
        )
    assert terminal.value.code == "CHILD_EXITED"

    monkeypatch.setattr(setup_wizard, "read_service_status", lambda _path: None)
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_a, **_k: _WizardHealthResponse(b'{"status":"starting"}'),
    )
    times = iter([0.0, 1.0, 6.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    with pytest.raises(setup_wizard.HostStartupError) as invalid:
        setup_wizard.wait_for_host_health(
            "127.0.0.1", 18765, timeout=5, service_managed=False
        )
    assert invalid.value.code == setup_wizard.HEALTH_TIMEOUT

    from app import __version__

    payload = (
        '{"status":"ok","mode":"personal","app_version":"'
        + __version__
        + '","sqlite":{"safe_version":true,"fts5":true}}'
    ).encode("utf-8")
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_a, **_k: _WizardHealthResponse(payload),
    )
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: 0.0)
    assert (
        setup_wizard.wait_for_host_health(
            "127.0.0.1", 18765, timeout=5, service_managed=False
        )
        == "http://127.0.0.1:18765"
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("missing", setup_wizard.SERVICE_MISSING),
        ("stopped", setup_wizard.SERVICE_STOPPED),
        ("unknown", setup_wizard.HEALTH_TIMEOUT),
    ],
)
def test_wizard_health_windows_service_timeout_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state: str,
    expected: str,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    times = iter([0.0, 6.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        setup_wizard,
        "_query_windows_host_service",
        lambda: (state, "SCM 诊断"),
    )
    monkeypatch.setattr(setup_wizard, "read_service_status", lambda _path: None)
    monkeypatch.setattr(setup_wizard, "tail_service_log", lambda _path: "日志")
    with pytest.raises(setup_wizard.HostStartupError) as caught:
        setup_wizard.wait_for_host_health(
            "192.168.1.8", 18765, timeout=5, data_dir=tmp_path
        )
    assert caught.value.code == expected and caught.value.detail == "SCM 诊断"


def test_wizard_health_status_file_overrides_generic_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    times = iter([0.0, 6.0])
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: next(times))
    statuses = [{"updated_at": "old", "code": "TLS_INIT_FAILED", "detail": "旧诊断"}]
    monkeypatch.setattr(
        setup_wizard,
        "read_service_status",
        lambda _path: (
            statuses.pop(0)
            if statuses
            else {
                "updated_at": "current",
                "code": "TLS_INIT_FAILED",
                "detail": "证书初始化失败",
            }
        ),
    )
    with pytest.raises(setup_wizard.HostStartupError) as caught:
        setup_wizard.wait_for_host_health(
            "192.168.1.8", 18765, timeout=5, data_dir=tmp_path
        )
    assert caught.value.code == "TLS_INIT_FAILED"


def test_windows_data_directory_unc_root_profile_and_fixed_disk_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(
        setup_wizard,
        "assert_windows_service_data_path_security",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        setup_wizard.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=4 * 1024**3),
    )
    with pytest.raises(ValueError, match="网络共享"):
        setup_wizard._validate_windows_data_dir(Path(r"\\server\share\PartyOps"))
    with pytest.raises(ValueError, match="磁盘根目录"):
        setup_wizard._validate_windows_data_dir(Path(tmp_path.anchor))

    profile = tmp_path / "Profiles" / "User"
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files x86"))
    with pytest.raises(ValueError, match="用户主目录"):
        setup_wizard._validate_windows_data_dir(profile / "PartyOps")

    import ctypes

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=lambda _root: 2)),
        raising=False,
    )
    with pytest.raises(ValueError, match="固定磁盘"):
        setup_wizard._validate_windows_data_dir(tmp_path / "removable")


def test_wizard_non_windows_guards_plain_config_and_device_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    setup_wizard._grant_windows_service_access(data_dir)
    setup_wizard._protect_windows_control_config(tmp_path / "partyops.env")
    setup_wizard._stop_windows_service_for_data_migration()
    assert not setup_wizard.windows_is_admin()
    setup_wizard.clear_windows_client_autostart()
    setup_wizard.clear_windows_personal_autostart()
    setup_wizard.install_windows_personal_autostart()

    config_root = tmp_path / "config"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config_root)
    monkeypatch.setattr(
        setup_wizard, "runtime_root", lambda: tmp_path / "runtime-without-key"
    )
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: [])
    config = setup_wizard.write_host_config(
        "127.0.0.1", 18765, data_dir, write_user_mode=False
    )
    assert config.is_file() and "PARTYOPS_UPDATE_PUBLIC_KEY" not in config.read_text(
        encoding="utf-8"
    )
    with pytest.raises(ValueError, match="个人模式端口"):
        setup_wizard.write_personal_config(data_dir, 80)

    monkeypatch.setattr(setup_wizard, "validate_config", lambda values: values)
    monkeypatch.setattr(setup_wizard, "write_mode_config", lambda *_a, **_k: None)
    device = setup_wizard.write_device_config(
        "https://192.168.1.8:18765",
        {"device_id": "device-1", "device_token": "token", "device_name": "协同机"},
        tmp_path / "backup",
        device_name="协同机",
    )
    payload = device.read_text(encoding="utf-8")
    assert '"device_id": "device-1"' in payload and '"ca_file"' not in payload


def test_windows_service_autostart_success_and_missing_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""),
    )
    setup_wizard._enable_windows_host_service_autostart()

    monkeypatch.setattr(
        setup_wizard, "_query_windows_host_service", lambda: ("missing", "未安装")
    )
    with pytest.raises(setup_wizard.HostStartupError) as missing:
        setup_wizard._start_windows_host_service(timeout=5)
    assert missing.value.code == setup_wizard.SERVICE_MISSING


def test_internal_ca_platform_guards_and_localhost_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard.sys, "platform", "darwin")
    setup_wizard.install_internal_ca(tmp_path / "missing-ca.pem")
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: tmp_path / "runtime")
    setup_wizard.install_internal_ca(tmp_path / "missing-ca.pem")

    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    with pytest.raises(ValueError, match="回环地址"):
        setup_wizard.resolve_host_url("localhost:18765")


def test_folder_picker_empty_result_missing_client_config_and_cli_guards(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(setup_wizard, "os", _os_proxy("posix"))
    monkeypatch.setattr(setup_wizard.shutil, "which", lambda _name: "/usr/bin/dialog")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess([], 1, "", ""),
    )
    assert setup_wizard._choose_system_folder() is None
    monkeypatch.setattr(
        setup_wizard, "config_root", lambda: tmp_path / "missing-config"
    )
    with pytest.raises(SystemExit, match="尚未配置"):
        setup_wizard.run_shared_root_manager(open_browser=False)

    monkeypatch.setattr(setup_wizard, "os", _os_proxy("nt"))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(
        setup_wizard.sys,
        "argv",
        ["wizard", "--privileged-host-config", "--host", "127.0.0.1"],
    )
    with pytest.raises(SystemExit, match="缺少 --data-dir"):
        setup_wizard.main()

    monkeypatch.setattr(
        setup_wizard.sys,
        "argv",
        ["wizard", "--manage-shared-roots", "--action-uri", "http://invalid"],
    )
    with pytest.raises(SystemExit, match="无效的本机共享操作地址"):
        setup_wizard.main()


def _write_client_backup(
    path: Path,
    *,
    manifest: object,
    members: dict[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        )
        for name, content in (members or {}).items():
            archive.writestr(name, content)


def _valid_backup_manifest(content: bytes = b"ok") -> dict[str, object]:
    return {
        "format": "partyops-backup",
        "files": [
            {
                "path": "database/partyops.db",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }


def test_client_backup_archive_limits_and_duplicate_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "backup.partyops-backup"
    _write_client_backup(
        archive_path,
        manifest=_valid_backup_manifest(),
        members={"database/partyops.db": b"ok"},
    )
    monkeypatch.setattr(client_agent, "MAX_BACKUP_MEMBERS", 0)
    with pytest.raises(ValueError, match="文件数量"):
        client_agent.verify_local_backup(archive_path)

    monkeypatch.setattr(client_agent, "MAX_BACKUP_MEMBERS", 10)
    monkeypatch.setattr(client_agent, "MAX_BACKUP_EXPANDED_BYTES", 0)
    with pytest.raises(ValueError, match="展开体积"):
        client_agent.verify_local_backup(archive_path)

    duplicate = tmp_path / "duplicate.partyops-backup"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("manifest.json", "{}")
    monkeypatch.setattr(client_agent, "MAX_BACKUP_EXPANDED_BYTES", 1024)
    with pytest.raises(ValueError, match="重复文件"):
        client_agent.verify_local_backup(duplicate)


def test_client_backup_manifest_rejects_defensive_shapes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.partyops-backup"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("database/partyops.db", b"ok")
    with pytest.raises(ValueError, match="清单缺失"):
        client_agent.verify_local_backup(missing)

    invalid_files = tmp_path / "invalid-files.partyops-backup"
    _write_client_backup(
        invalid_files,
        manifest={"format": "partyops-backup", "files": "not-a-list"},
    )
    with pytest.raises(ValueError, match="文件列表无效"):
        client_agent.verify_local_backup(invalid_files)

    invalid_item = tmp_path / "invalid-item.partyops-backup"
    _write_client_backup(
        invalid_item,
        manifest={"format": "partyops-backup", "files": ["invalid"]},
    )
    with pytest.raises(ValueError, match="文件记录无效"):
        client_agent.verify_local_backup(invalid_item)

    invalid_fields = tmp_path / "invalid-fields.partyops-backup"
    _write_client_backup(
        invalid_fields,
        manifest={
            "format": "partyops-backup",
            "files": [{"path": "database/partyops.db"}],
        },
    )
    with pytest.raises(ValueError, match="校验字段无效"):
        client_agent.verify_local_backup(invalid_fields)

    illegal_path = tmp_path / "illegal-path.partyops-backup"
    _write_client_backup(
        illegal_path,
        manifest={
            "format": "partyops-backup",
            "files": [{"path": "..\\escape.db", "size": 0, "sha256": "0" * 64}],
        },
    )
    with pytest.raises(ValueError, match="非法路径"):
        client_agent.verify_local_backup(illegal_path)

    invalid_hash = tmp_path / "invalid-hash.partyops-backup"
    _write_client_backup(
        invalid_hash,
        manifest={
            "format": "partyops-backup",
            "files": [{"path": "database/partyops.db", "size": -1, "sha256": "z" * 64}],
        },
    )
    with pytest.raises(ValueError, match="校验字段无效"):
        client_agent.verify_local_backup(invalid_hash)


def test_client_backup_detects_overrun_missing_database_and_unexpected_member(
    tmp_path: Path,
) -> None:
    overrun = tmp_path / "overrun.partyops-backup"
    _write_client_backup(
        overrun,
        manifest={
            "format": "partyops-backup",
            "files": [{"path": "database/partyops.db", "size": 1, "sha256": "0" * 64}],
        },
        members={"database/partyops.db": b"too-large"},
    )
    with pytest.raises(ValueError, match="超过清单大小"):
        client_agent.verify_local_backup(overrun)

    missing_db = tmp_path / "missing-db.partyops-backup"
    content = b"other"
    _write_client_backup(
        missing_db,
        manifest={
            "format": "partyops-backup",
            "files": [
                {
                    "path": "files/other.txt",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        },
        members={"files/other.txt": content},
    )
    with pytest.raises(ValueError, match="缺少 PartyOps 数据库"):
        client_agent.verify_local_backup(missing_db)

    unexpected = tmp_path / "unexpected.partyops-backup"
    _write_client_backup(
        unexpected,
        manifest=_valid_backup_manifest(),
        members={"database/partyops.db": b"ok", "unlisted.txt": b"extra"},
    )
    with pytest.raises(ValueError, match="未在清单登记"):
        client_agent.verify_local_backup(unexpected)


def test_client_response_filename_and_safe_root_filtering(tmp_path: Path) -> None:
    assert (
        client_agent._response_filename("attachment; filename*=UTF-8%20name.zip")
        == "UTF-8 name.zip"
    )
    assert (
        client_agent._response_filename("attachment")
        == "PartyOps-latest.partyops-backup"
    )
    valid = tmp_path / "共享目录"
    valid.mkdir()
    roots = client_agent._safe_shared_roots(
        {
            "shared_roots": [
                "invalid",
                {"local_path": str(tmp_path / "missing"), "remote_key": "missing"},
                {"local_path": str(valid), "remote_key": ""},
                {"local_path": str(valid), "remote_key": "bad/key"},
                {"local_path": str(valid), "remote_key": "valid_key"},
            ]
        }
    )
    assert len(roots) == 1 and roots[0]["remote_key"] == "valid_key"


def test_client_enrollment_cache_retry_and_invalid_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code = f"{'Ab_9-xYz0123456789QwErTy'[:24]}.{'A1' * 32}"
    normalized = client_agent.normalize_enrollment_code(code)
    pending = tmp_path / "pending.json"
    identity = {
        "host_url": "http://127.0.0.1:18765",
        "device_name": "协同机",
        "code_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }
    pending.write_text(
        json.dumps({**identity, "result": {"device_token": "cached-token"}}),
        encoding="utf-8",
    )
    assert (
        client_agent.enroll_device(
            identity["host_url"], code, identity["device_name"], pending_path=pending
        )["device_token"]
        == "cached-token"
    )

    pending.unlink()
    calls = 0

    def retry_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("temporary")
        return {"device_token": "new-token"}

    monkeypatch.setattr(client_agent, "_json_request", retry_request)
    monkeypatch.setattr(
        client_agent, "device_metadata", lambda: {"platform": "windows"}
    )
    monkeypatch.setattr(client_agent.time, "sleep", lambda _seconds: None)
    assert (
        client_agent.enroll_device(
            identity["host_url"], code, identity["device_name"], pending_path=pending
        )["device_token"]
        == "new-token"
    )
    assert calls == 2

    pending.unlink()
    monkeypatch.setattr(client_agent, "_json_request", lambda *_a, **_k: [])
    with pytest.raises(ValueError, match="有效设备凭据"):
        client_agent.enroll_device(
            identity["host_url"], code, identity["device_name"], pending_path=pending
        )


def test_client_records_auth_failure_and_survives_state_write_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config: dict[str, object] = {}
    response = io.BytesIO(b"{}")
    error = urllib.error.HTTPError("http://host", 401, "denied", {}, response)
    monkeypatch.setattr(
        client_agent,
        "_save_config",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("read only")),
    )
    client_agent._record_agent_failure(
        tmp_path / "config.json", config, "heartbeat", error
    )
    assert config["authentication_state"] == "reauth_required"


def test_client_run_exercises_background_state_machine_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    shared = tmp_path / "共享目录"
    shared.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "host_url": "http://127.0.0.1:18765",
                "agent_url": "http://127.0.0.1:18765",
                "device_id": "device-1",
                "device_token": "token",
                "backup_dir": str(tmp_path / "backup"),
                "open_browser": False,
                "shared_roots": [
                    {
                        "local_path": str(shared),
                        "remote_key": "root_key",
                        "root_id": "root-1",
                        "approval_status": "approved",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeThread:
        def __init__(self, **_kwargs) -> None:
            self.started = False
            self.joined = False

        def start(self) -> None:
            self.started = True

        def join(self, timeout: int) -> None:
            assert timeout == 2
            self.joined = True

    class FakeFormatterService:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        def start(self):
            return self

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(client_agent, "configure_agent_logging", lambda _path: None)
    monkeypatch.setattr(client_agent, "configure_ssl_context", lambda _config: None)
    monkeypatch.setattr(client_agent, "send_device_heartbeat", lambda *_a, **_k: None)
    monkeypatch.setattr(
        official_format_service,
        "OfficialFormatLocalService",
        FakeFormatterService,
    )
    monkeypatch.setattr(client_agent.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        client_agent,
        "sync_shared_roots",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("同步失败")),
    )
    monkeypatch.setattr(
        client_agent, "poll_device_commands", lambda *_a, **_k: [{"id": "1"}]
    )
    monkeypatch.setattr(client_agent, "process_device_command", lambda *_a, **_k: True)
    monkeypatch.setattr(client_agent, "pull_backup", lambda *_a, **_k: None)
    monkeypatch.setattr(
        client_agent, "scan_and_upload_roots", lambda *_a, **_k: (2, ["部分失败"])
    )
    monkeypatch.setattr(client_agent, "_save_config", lambda *_a, **_k: None)
    monkeypatch.setattr(
        client_agent, "poll_desktop_notifications", lambda *_a, **_k: True
    )
    # client_agent.time 指向标准库模块；直接修改其属性会污染并发运行的公文
    # 排版清理线程。替换当前模块引用即可覆盖循环，又不会影响其他线程。
    monkeypatch.setattr(
        client_agent,
        "time",
        SimpleNamespace(
            monotonic=lambda: 100.0,
            sleep=lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop-loop")),
        ),
    )
    with pytest.raises(RuntimeError, match="stop-loop"):
        client_agent.run(config_path, once=False, open_browser=False)


class _ScalarResult:
    def __init__(self, values) -> None:
        self.values = list(values)

    def all(self):
        return self.values


def test_security_bearer_expired_revoked_disabled_and_optional_paths() -> None:
    now = datetime.now(timezone.utc)
    bearer_request = SimpleNamespace(
        cookies={},
        headers={"authorization": "Bearer bearer-token"},
    )

    class SecurityDb:
        def __init__(self, record=None, user=None) -> None:
            self.record = record
            self.user = user

        def scalar(self, _statement):
            return self.record

        def get(self, _model, _identity):
            return self.user

    with pytest.raises(ProblemException) as missing:
        security.get_current_user(bearer_request, SecurityDb())
    assert missing.value.code == "SESSION_INVALID"

    revoked = SimpleNamespace(revoked_at=now, expires_at=now, user_id="u1")
    with pytest.raises(ProblemException) as invalid:
        security.get_current_user(bearer_request, SecurityDb(revoked))
    assert invalid.value.code == "SESSION_INVALID"

    expired = SimpleNamespace(
        revoked_at=None,
        expires_at=now
        - timezone.utc.utcoffset(now)
        - __import__("datetime").timedelta(seconds=1),
        user_id="u1",
    )
    with pytest.raises(ProblemException) as stale:
        security.get_current_user(bearer_request, SecurityDb(expired))
    assert stale.value.code == "SESSION_EXPIRED"

    valid_record = SimpleNamespace(
        revoked_at=None,
        expires_at=now + __import__("datetime").timedelta(hours=1),
        user_id="u1",
        last_seen_at=None,
    )
    disabled = SimpleNamespace(active=False)
    with pytest.raises(ProblemException) as denied:
        security.get_current_user(bearer_request, SecurityDb(valid_record, disabled))
    assert denied.value.code == "USER_DISABLED"

    cookie_request = SimpleNamespace(cookies={security.SESSION_COOKIE: "token"})
    assert (
        security.get_current_user_optional(cookie_request, SecurityDb(revoked)) is None
    )


def test_version_and_router_utility_unreachable_guard_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        versioning,
        "Version",
        lambda _value: SimpleNamespace(epoch=1, local=None, release=(1, 2, 3)),
    )
    with pytest.raises(ProblemException) as invalid:
        versioning.parse_release_version("1.2.3")
    assert invalid.value.code == "UPDATE_VERSION_INVALID"
    with pytest.raises(ProblemException) as required:
        router_utils.parse_if_match(None)
    assert required.value.code == "IF_MATCH_REQUIRED"


def _device(
    *, active: bool = True, status: str = "online", allow_host_access: bool = True
):
    return SimpleNamespace(
        id="device-1",
        active=active,
        status=status,
        allow_host_access=allow_host_access,
    )


class _WorkspaceDb:
    def __init__(self, *, device=None, root=None, member=None, grants=()) -> None:
        self.device = device
        self.root = root
        self.member = member
        self.grants = list(grants)

    def get(self, model, _identity):
        if model is Device:
            return self.device
        if model is WorkspaceRoot:
            return self.root
        return None

    def scalar(self, _statement):
        return self.member

    def scalars(self, _statement):
        return _ScalarResult(self.grants)


def test_workspace_access_rejects_inactive_wrong_and_unapproved_roots() -> None:
    user = SimpleNamespace(id="u1", role=SimpleNamespace(value="member"))
    assert not workspace_access.grant_allows(
        _WorkspaceDb(device=_device(active=False)), user, "device-1", None, "download"
    )
    assert not workspace_access.grant_allows(
        _WorkspaceDb(device=_device()), user, "device-1", "missing", "download"
    )
    wrong = SimpleNamespace(
        id="root-1",
        enabled=True,
        source=SimpleNamespace(value="device"),
        device_id="another-device",
        approval_status="approved",
    )
    assert not workspace_access.grant_allows(
        _WorkspaceDb(device=_device(), root=wrong),
        user,
        "device-1",
        "root-1",
        "download",
    )


def test_workspace_access_member_fallback_and_host_device_policy() -> None:
    user = SimpleNamespace(id="u1", role=SimpleNamespace(value="member"))
    root = SimpleNamespace(
        id="root-1",
        enabled=True,
        source=SimpleNamespace(value="device"),
        device_id="device-1",
        approval_status="approved",
        published_by_user_id="other",
        share_scope="members",
    )
    member = SimpleNamespace(can_download=False, can_send=False, can_browse=True)
    grant = SimpleNamespace(capabilities={"*"})
    assert workspace_access.grant_allows(
        _WorkspaceDb(device=_device(), root=root, member=member, grants=[grant]),
        user,
        "device-1",
        "root-1",
        "download",
    )

    host_root = SimpleNamespace(
        enabled=True,
        source=SimpleNamespace(value="host"),
    )
    denied = workspace_access.workspace_root_permissions(
        _WorkspaceDb(device=_device(allow_host_access=False)),
        host_root,
        user,
        current_device_id="device-1",
    )
    assert not denied["browse"]

    unavailable = SimpleNamespace(
        enabled=True,
        source=SimpleNamespace(value="device"),
        approval_status="approved",
        device_id="device-1",
    )
    assert not workspace_access.workspace_root_permissions(
        _WorkspaceDb(device=_device(status="revoked")), unavailable, user
    )["browse"]


class _CalendarDb:
    def __init__(self, result_sets) -> None:
        self.result_sets = list(result_sets)

    def scalars(self, _statement):
        return _ScalarResult(self.result_sets.pop(0) if self.result_sets else [])


def _calendar_task(identifier: str, instant: datetime, *, owner="u1", area="area"):
    return SimpleNamespace(
        id=identifier,
        title=identifier,
        owner_id=owner,
        work_area=area,
        formal_due_at=instant,
        internal_due_at=None,
        planned_start_at=None,
        planned_end_at=None,
        status=SimpleNamespace(value="in_progress"),
        priority=SimpleNamespace(value="normal"),
    )


def test_calendar_filter_and_range_defensive_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    end = start + __import__("datetime").timedelta(days=1)
    monkeypatch.setattr(
        calendar_service,
        "visible_tasks",
        lambda *_a: [
            _calendar_task("wrong-owner", start, owner="u2"),
            _calendar_task("wrong-area", start, area="other"),
            _calendar_task("outside", end + __import__("datetime").timedelta(hours=1)),
        ],
    )
    monkeypatch.setattr(calendar_service, "_topic_ids", lambda *_a: {})
    events = calendar_service.calendar_events(
        _CalendarDb([[]]),
        SimpleNamespace(id="u1", role=UserRole.STAFF),
        start,
        end,
        event_types={CalendarEventType.TASK_DUE},
        owner_ids={"u1"},
        work_areas={"area"},
    )
    assert events == []


def test_calendar_recurrence_and_workday_skip_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 8, 15, tzinfo=timezone.utc)
    end = start + __import__("datetime").timedelta(days=1)
    monkeypatch.setattr(calendar_service, "visible_tasks", lambda *_a: [])
    monkeypatch.setattr(calendar_service, "_topic_ids", lambda *_a: {})
    rule = SimpleNamespace(
        id="r1",
        owner_id="other",
        name="周期",
        next_run_at=start,
        paused_until=None,
        kind=SimpleNamespace(value="daily"),
    )
    workday = SimpleNamespace(
        id="w1",
        is_workday=True,
        date_key="2026-08-15",
        title="调休",
        kind="adjusted",
        owner_id=None,
        note="",
    )
    events = calendar_service.calendar_events(
        _CalendarDb([[rule], [workday]]),
        SimpleNamespace(id="u1", role=UserRole.STAFF),
        start,
        end,
        event_types={CalendarEventType.RECURRENCE},
        owner_ids={"u1"},
    )
    assert events == []

    holiday = SimpleNamespace(
        id="w2",
        is_workday=False,
        date_key="2026-09-01",
        title="节日",
        kind="holiday",
        owner_id=None,
        note="",
    )
    events = calendar_service.calendar_events(
        _CalendarDb([[holiday]]),
        SimpleNamespace(id="u1", role=UserRole.ADMIN),
        start,
        end,
        event_types={CalendarEventType.HOLIDAY},
    )
    assert events == []


def test_recurrence_extension_rejection_matrix() -> None:
    member = SimpleNamespace(id="u1", role=UserRole.STAFF)
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    request = SimpleNamespace(client=None)

    with pytest.raises(ProblemException) as missing:
        recurrence_extensions.get_recurrence_preview(
            "missing", user=member, db=_LookupDb()
        )
    assert missing.value.code == "RECURRENCE_NOT_FOUND"

    rule = SimpleNamespace(id="r1", owner_id="other", version=2)
    with pytest.raises(ProblemException):
        recurrence_extensions.get_recurrence_preview(
            "r1", user=member, db=_LookupDb({"r1": rule})
        )

    payload = SimpleNamespace(
        action=RecurrenceExceptionAction.SKIP,
        occurrence_at=datetime.now(timezone.utc),
        rescheduled_at=None,
        reason="冲突",
    )
    with pytest.raises(ProblemException) as version_error:
        recurrence_extensions.create_recurrence_exception(
            "r1",
            payload,
            request,
            if_match="1",
            admin=admin,
            db=_LookupDb({"r1": rule}),
        )
    assert version_error.value.code == "VERSION_CONFLICT"

    payload.action = RecurrenceExceptionAction.RESCHEDULE
    with pytest.raises(ProblemException) as time_error:
        recurrence_extensions.create_recurrence_exception(
            "r1",
            payload,
            request,
            if_match="2",
            admin=admin,
            db=_LookupDb({"r1": rule}),
        )
    assert time_error.value.code == "RESCHEDULE_TIME_REQUIRED"

    payload.action = RecurrenceExceptionAction.SKIP
    existing_db = _LookupDb({"r1": rule})
    existing_db.scalar = lambda _statement: SimpleNamespace(id="existing")
    with pytest.raises(ProblemException) as duplicate:
        recurrence_extensions.create_recurrence_exception(
            "r1", payload, request, if_match="2", admin=admin, db=existing_db
        )
    assert duplicate.value.code == "RECURRENCE_EXCEPTION_EXISTS"


def test_update_process_and_lock_staleness_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assert not update_executor._process_is_running(0)

    def missing_process(_pid: int, _signal: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(update_executor, "os", _os_proxy("posix", kill=missing_process))
    assert not update_executor._process_is_running(123)

    def denied_process(_pid: int, _signal: int) -> None:
        raise PermissionError

    monkeypatch.setattr(update_executor, "os", _os_proxy("posix", kill=denied_process))
    assert update_executor._process_is_running(123)

    lock = tmp_path / "update.lock"
    assert update_executor._update_lock_is_stale(lock)
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(update_executor.time, "time", lambda: lock.stat().st_mtime)
    assert not update_executor._update_lock_is_stale(lock)
    monkeypatch.setattr(
        update_executor.time,
        "time",
        lambda: (
            lock.stat().st_mtime + update_executor.LEGACY_UPDATE_LOCK_GRACE_SECONDS + 1
        ),
    )
    assert update_executor._update_lock_is_stale(lock)

    lock.write_text("invalid-json", encoding="utf-8")
    assert update_executor._update_lock_is_stale(lock)
    lock.write_text(json.dumps({"pid": 123, "boot_id": "old"}), encoding="utf-8")
    monkeypatch.setattr(update_executor, "_system_boot_id", lambda: "new")
    assert update_executor._update_lock_is_stale(lock)


def test_update_lock_link_and_exhausted_stale_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "update.lock"
    monkeypatch.setattr(
        update_executor, "_is_link_or_reparse_point", lambda path: path == lock.parent
    )
    assert not update_executor._acquire_update_lock(lock)

    monkeypatch.setattr(
        update_executor, "_is_link_or_reparse_point", lambda _path: False
    )
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: True)
    monkeypatch.setattr(
        update_executor.os,
        "open",
        lambda *_a, **_k: (_ for _ in ()).throw(FileExistsError),
    )
    monkeypatch.setattr(Path, "unlink", lambda *_a, **_k: None)
    assert not update_executor._acquire_update_lock(lock)


def test_update_platform_environment_and_run_status_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(update_executor, "detect_platform_info", lambda: {})
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "")
    with pytest.raises(RuntimeError, match="无法匹配"):
        update_executor._manifest_platform_name({"format_version": 3})

    captured = {}
    monkeypatch.setattr(
        update_executor.subprocess,
        "run",
        lambda command, **kwargs: (
            captured.update({"command": command, **kwargs})
            or subprocess.CompletedProcess(command, 0, "", "")
        ),
    )
    update_executor._run(["helper"], environment={"PARTYOPS_TEST": "1"})
    assert captured["env"]["PARTYOPS_TEST"] == "1"

    class RunDb:
        def __init__(self, run) -> None:
            self.run = run
            self.commits = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _identity):
            return self.run

        def commit(self):
            self.commits += 1

    missing_db = RunDb(None)
    monkeypatch.setattr(
        update_executor.db_runtime, "session_factory", lambda: missing_db
    )
    update_executor._set_run(
        "missing", status=UpdateStatus.APPLYING, progress=200, message="x"
    )
    assert missing_db.commits == 0

    run = SimpleNamespace(status=None, progress=0, message="", completed_at=None)
    run_db = RunDb(run)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: run_db)
    update_executor._set_run(
        "run", status=UpdateStatus.COMPLETED, progress=200, message="完成" * 2000
    )
    assert (
        run.progress == 100
        and run.completed_at is not None
        and len(run.message) == 2000
    )


def test_update_downgrade_minimum_and_package_manager_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(app_version="1.4.3-rc.3"),
    )
    with pytest.raises(RuntimeError, match="降级"):
        update_executor._assert_update_not_downgrade({"version": "1.4.3-rc.2"})
    with pytest.raises(RuntimeError, match="最低兼容版本号无效"):
        update_executor._assert_update_not_downgrade(
            {"version": "1.4.3-rc.3", "min_version": "invalid"}
        )
    with pytest.raises(RuntimeError, match="高于目标版本"):
        update_executor._assert_update_not_downgrade(
            {"version": "1.4.3-rc.3", "min_version": "1.4.4"}
        )

    monkeypatch.setattr(update_executor.shutil, "which", lambda _name: None)
    assert not update_executor._install_rpm(tmp_path / "package.rpm")
    monkeypatch.setattr(
        update_executor.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda *_a, **_k: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert update_executor._install_rpm(tmp_path / "package.rpm", allow_downgrade=True)


def test_update_artifact_selection_rejects_platform_shape_and_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        update_executor, "_verify_manifest_signature", lambda _manifest: True
    )
    monkeypatch.setattr(
        update_executor, "_assert_update_not_downgrade", lambda _manifest: None
    )
    package = tmp_path / "package.partyops-update"
    package.write_bytes(b"unused")
    with pytest.raises(RuntimeError, match="不包含"):
        update_executor._select_artifact(
            package,
            {"platform_artifacts": [], "artifacts": {}},
            "amd64",
            tmp_path / "out",
            "windows",
        )
    with pytest.raises(RuntimeError, match="没有允许"):
        update_executor._select_artifact(
            package,
            {
                "platform_artifacts": {"unknown": {"amd64": "file.bin"}},
                "artifacts": {"file.bin": {}},
            },
            "amd64",
            tmp_path / "out",
            "unknown",
        )
    with pytest.raises(RuntimeError, match="清单不一致"):
        update_executor._select_artifact(
            package,
            {
                "platform_artifacts": {"windows": {"amd64": "wrong.exe"}},
                "artifacts": {"wrong.exe": {}},
            },
            "amd64",
            tmp_path / "out",
            "windows",
        )


def test_update_health_windows_service_and_manifest_platform_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    checks = iter([False, True])
    monkeypatch.setattr(update_executor, "_health_check", lambda _version: next(checks))
    monkeypatch.setattr(update_executor.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(update_executor.time, "sleep", lambda _seconds: None)
    assert update_executor._wait_for_health("1.4.3-rc.3", 1)

    monkeypatch.setattr(
        update_executor, "detect_platform_info", lambda: {"family": "windows7"}
    )
    monkeypatch.setattr(
        update_executor, "update_platform_key", lambda _info: "windows7"
    )
    assert update_executor._manifest_has_windows_artifact(
        {
            "format_version": 3,
            "platform_artifacts": {"windows7": {"x86": "legacy.exe"}},
        },
        "x86",
    )
    monkeypatch.setattr(
        update_executor, "update_platform_key", lambda _info: "linux-deb"
    )
    assert not update_executor._manifest_has_windows_artifact(
        {"format_version": 3, "platform_artifacts": {}}, "amd64"
    )

    installer = tmp_path / "PartyOpsService.exe"
    monkeypatch.setattr(
        update_executor.sys, "executable", str(tmp_path / "partyops.exe")
    )
    installer.write_bytes(b"service")
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_k: subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert update_executor._stop_windows_host_service() == (True, True)
    installer.unlink()
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_k: subprocess.CompletedProcess(command, 1062, "", ""),
    )
    assert update_executor._stop_windows_host_service() == (True, False)


def test_update_environment_parser_and_supervisor_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / "partyops.env"
    environment.write_text(
        "# comment\nINVALID=value\nPARTYOPS_MODE=host\nPARTYOPS_QUOTED='data value'\nPARTYOPS_BAD='unterminated\n",
        encoding="utf-8",
    )
    assert update_executor._read_environment(environment) == {
        "PARTYOPS_MODE": "host",
        "PARTYOPS_QUOTED": "data value",
    }
    assert update_executor._trusted_system_environment_file(tmp_path / "missing")

    monkeypatch.setattr(update_executor, "_candidate_host_environments", lambda: [])
    assert update_executor.run_supervisor(once=True) == 0
