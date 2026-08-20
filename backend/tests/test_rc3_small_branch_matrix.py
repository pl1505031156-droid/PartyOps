"""补齐 rc.3 跨平台探测、Legacy 兼容层与启动诊断的分支回归。"""

from __future__ import annotations

import asyncio
import os
import typing
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import (
    compat,
    config,
    database,
    platform_info,
    startup_diagnostics,
    windows_host_status,
)


def test_legacy_to_thread_and_strict_zip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat.asyncio, "to_thread", None)
    assert asyncio.run(compat.to_thread(lambda value: value + 1, 2)) == 3

    monkeypatch.setattr(compat.sys, "version_info", (3, 8, 10))
    assert list(compat.strict_zip([1, 2], [3, 4])) == [(1, 3), (2, 4)]
    with pytest.raises(ValueError, match="长度不一致"):
        list(compat.strict_zip([1], [2, 3]))


def test_legacy_typing_aliases_come_from_official_backport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compat.sys, "version_info", (3, 8, 10))
    monkeypatch.delattr(typing, "Annotated")
    compat.install_legacy_typing_aliases()
    assert hasattr(typing, "Annotated")


def test_default_data_dir_all_platform_branches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "显式 数据"
    monkeypatch.setenv("PARTYOPS_DATA_DIR", str(explicit))
    assert config.default_data_dir() == explicit.resolve()

    monkeypatch.delenv("PARTYOPS_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr(
        config,
        "os",
        SimpleNamespace(name="nt", getenv=os.getenv),
    )
    assert config.default_data_dir() == (tmp_path / "Local" / "PartyOps").resolve()

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(
        config,
        "os",
        SimpleNamespace(name="posix", getenv=os.getenv),
    )
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config.default_data_dir() == (
        Path.home() / "Library" / "Application Support" / "PartyOps" / "Data"
    ).resolve()
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.default_data_dir() == (tmp_path / "xdg" / "partyops").resolve()


def test_platform_info_invalid_os_release_and_platform_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = tmp_path / "os-release"
    release.write_text(
        "# 注释\nBAD-KEY=ignored\nID='deepin'\nID_LIKE=\"debian ubuntu\"\nVERSION_ID=25\n",
        encoding="utf-8",
    )
    values = platform_info.read_os_release(release)
    assert "BAD-KEY" not in values and values["ID"] == "deepin"
    assert platform_info._linux_package_format(values) == "deb"
    assert platform_info._linux_package_format({"ID": "unknown"}) == ""

    monkeypatch.setattr(platform_info, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        platform_info,
        "platform",
        SimpleNamespace(machine=lambda: "i686", win32_ver=lambda: ("7", "6.1", "", "")),
    )
    windows7 = platform_info.detect_platform_info()
    assert windows7["runtime_profile"] == "legacy-core"
    assert "semantic_rerank" not in windows7["capabilities"]

    monkeypatch.setattr(platform_info, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(
        platform_info,
        "platform",
        SimpleNamespace(machine=lambda: "aarch64"),
    )
    linux = platform_info.detect_platform_info(os_release_path=release)
    assert linux["architecture"] == "arm64"
    assert platform_info.update_platform_key(linux) == "linux-deb"

    monkeypatch.setattr(platform_info, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        platform_info,
        "platform",
        SimpleNamespace(
            machine=lambda: "x86_64",
            mac_ver=lambda: ("14.7.1", ("", "", ""), ""),
        ),
    )
    macos = platform_info.detect_platform_info()
    assert macos == {
        "platform_family": "macos",
        "distribution": "macos",
        "distribution_version": "14.7.1",
        "package_format": "pkg",
        "architecture": "amd64",
        "runtime_profile": "full",
        "capabilities": [
            *platform_info.CORE_CAPABILITIES,
            *platform_info.AI_CAPABILITIES,
        ],
        "platform": "macos",
    }
    assert platform_info.update_platform_key(macos) == "macos"
    assert platform_info.update_platform_key({"platform_family": "plan9"}) == ""


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("no such column: version", startup_diagnostics.DATABASE_SCHEMA_FAILED),
        ("database or disk is full", startup_diagnostics.DATA_DIR_FULL),
        ("unable to open database file: permission denied", startup_diagnostics.DATABASE_IO_FAILED),
        ("an unknown sqlite failure", startup_diagnostics.DATABASE_STARTUP_FAILED),
    ],
)
def test_database_startup_diagnostic_matrix(message: str, expected: str) -> None:
    code, public = startup_diagnostics.classify_database_startup_error(RuntimeError(message))
    assert code == expected and public == startup_diagnostics.PUBLIC_STARTUP_MESSAGES[expected]
    assert "traceback" not in public.lower()


def test_windows_host_status_optional_fields_and_invalid_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = windows_host_status.write_service_status(
        tmp_path,
        stage="child_started",
        pid=123,
        exit_code=5,
        detail="诊断",
    )
    payload = windows_host_status.read_service_status(tmp_path)
    assert payload and payload["pid"] == 123 and payload["exit_code"] == 5

    path.write_text("[]", encoding="utf-8")
    assert windows_host_status.read_service_status(tmp_path) is None
    path.write_text('{"format_version": 2}', encoding="utf-8")
    assert windows_host_status.read_service_status(tmp_path) is None

    log = windows_host_status.service_log_path(tmp_path)
    log.write_text("首行\n第二行\n第三行", encoding="utf-8")
    assert "首行" not in windows_host_status.tail_service_log(tmp_path, max_bytes=16)

    assert windows_host_status.probe_loopback_health(80, tls=False)[0] is False
    assert windows_host_status.probe_loopback_health(18765, tls=True)[1] == "TLS 健康检查缺少 PartyOps 内部 CA"
    missing_ca = tmp_path / "missing-ca.pem"
    assert "尚未生成" in windows_host_status.probe_loopback_health(
        18765, tls=True, ca_file=missing_ca
    )[1]
    assert windows_host_status.health_payload_ready([]) is False
    assert windows_host_status.health_payload_ready(
        {
            "status": "ok",
            "mode": "host",
            "app_version": "1.4.3-rc.3",
            "sqlite": {"safe_version": True, "fts5": True},
        },
        expected_version="1.4.3-rc.2",
    ) is False


def test_database_session_events_serialize_all_write_paths() -> None:
    class CountingLock:
        def __init__(self) -> None:
            self.acquired = 0
            self.released = 0

        def acquire(self) -> None:
            self.acquired += 1

        def release(self) -> None:
            self.released += 1

    class Runtime:
        def __init__(self) -> None:
            self.write_lock = CountingLock()
            self.entered = 0
            self.left = 0

        def enter_session_activity(self, _session) -> None:
            self.entered += 1

        def leave_session_activity(self, _session) -> None:
            self.left += 1

    runtime = Runtime()
    session = SimpleNamespace(info={"partyops_database_runtime": runtime})
    read_state = SimpleNamespace(
        session=session,
        is_insert=False,
        is_update=False,
        is_delete=False,
    )
    database.register_orm_activity(read_state)
    assert runtime.write_lock.acquired == 0

    write_state = SimpleNamespace(
        session=session,
        is_insert=True,
        is_update=False,
        is_delete=False,
    )
    database.register_orm_activity(write_state)
    database.register_flush_activity(session, None, None)
    assert runtime.write_lock.acquired == 1

    # 保存点结束不能提前释放外层写事务；最外层事务结束时必须精确释放一次。
    database.release_orm_activity(session, SimpleNamespace(parent=object()))
    assert runtime.write_lock.released == 0
    database.release_orm_activity(session, SimpleNamespace(parent=None))
    assert runtime.left == 1
    assert runtime.write_lock.released == 1
    assert "partyops_write_lock_held" not in session.info

    # 没有运行时标记的第三方 Session 不参与 PartyOps 串行化。
    external = SimpleNamespace(info={})
    database.register_flush_activity(external, None, None)
    database.release_orm_activity(external, SimpleNamespace(parent=None))
