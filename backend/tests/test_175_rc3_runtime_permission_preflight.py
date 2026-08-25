"""个人模式权限预检与桌面账号安装探针回归。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import setup_wizard, startup_selftest
from app.windows_host_status import RUNTIME_PERMISSION_DENIED


def _runtime_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"MZ-partyops")
    executable.chmod(0o700)
    config = tmp_path / "personal.env"
    config.write_text(
        f"PARTYOPS_DATA_DIR={tmp_path / 'data'}\nPARTYOPS_PORT=18775\n",
        encoding="utf-8",
    )
    return executable, config, tmp_path / "data"


def test_personal_runtime_permission_preflight_covers_atomic_write_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, config, data_dir = _runtime_files(tmp_path)
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    assert (
        setup_wizard._preflight_personal_runtime_access(config, data_dir)
        == executable
    )
    assert data_dir.is_dir()
    assert (data_dir / "launcher.log").is_file()
    assert not list(data_dir.glob(".partyops-runtime-permission-*"))


def test_personal_runtime_preflight_reports_executable_and_data_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, config, data_dir = _runtime_files(tmp_path)
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    original_access = os.access
    monkeypatch.setattr(setup_wizard.os, "access", lambda *_args: False)
    with pytest.raises(setup_wizard.HostStartupError) as executable_denied:
        setup_wizard._preflight_personal_runtime_access(config, data_dir)
    assert executable_denied.value.code == RUNTIME_PERMISSION_DENIED
    assert "PartyOps 主程序" in str(executable_denied.value)

    monkeypatch.setattr(setup_wizard.os, "access", original_access)
    monkeypatch.setattr(
        setup_wizard,
        "_write_private",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PermissionError(13, "permission denied")
        ),
    )
    with pytest.raises(setup_wizard.HostStartupError) as data_denied:
        setup_wizard._preflight_personal_runtime_access(config, data_dir)
    assert data_denied.value.code == RUNTIME_PERMISSION_DENIED
    assert "个人数据目录" in str(data_denied.value)
    assert str(data_dir) in data_denied.value.detail


def test_personal_runtime_preflight_rejects_missing_and_empty_control_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, config, data_dir = _runtime_files(tmp_path)
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: executable)
    config.unlink()
    with pytest.raises(setup_wizard.HostStartupError) as missing:
        setup_wizard._preflight_personal_runtime_access(config, data_dir)
    assert missing.value.code == RUNTIME_PERMISSION_DENIED
    assert "个人模式配置" in str(missing.value)

    config.write_bytes(b"")
    with pytest.raises(setup_wizard.HostStartupError) as empty:
        setup_wizard._preflight_personal_runtime_access(config, data_dir)
    assert empty.value.code == RUNTIME_PERMISSION_DENIED


def test_launch_personal_stops_before_spawn_when_permission_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executable, config, data_dir = _runtime_files(tmp_path)
    monkeypatch.setattr(
        setup_wizard,
        "load_host_environment",
        lambda _path: {
            "PARTYOPS_PORT": "18775",
            "PARTYOPS_DATA_DIR": str(data_dir),
        },
    )
    monkeypatch.setattr(
        setup_wizard,
        "_preflight_personal_runtime_access",
        lambda *_args: (_ for _ in ()).throw(
            setup_wizard.HostStartupError(
                RUNTIME_PERMISSION_DENIED,
                "当前账号没有启动权限。",
                detail="阶段=PartyOps 主程序",
            )
        ),
    )
    monkeypatch.setattr(
        setup_wizard,
        "_spawn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("权限预检失败后不得创建子进程")
        ),
    )
    with pytest.raises(setup_wizard.HostStartupError) as denied:
        setup_wizard.launch_personal(config)
    assert denied.value.code == RUNTIME_PERMISSION_DENIED


def test_original_desktop_user_permission_selftest_is_side_effect_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    frontend = runtime / "_internal" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("<!doctype html>", encoding="utf-8")
    executable = runtime / "PartyOps.exe"
    executable.write_bytes(b"MZ")
    monkeypatch.setattr(startup_selftest.sys, "executable", str(executable))
    result = startup_selftest.run_user_permission_selftest(runtime)
    assert result == {
        "passed": True,
        "mode": "desktop-user-permission",
        "runtime_readable": True,
        "user_temp_writable": True,
    }


def test_original_desktop_user_permission_selftest_rejects_bad_resources_and_writeback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.exe"
    monkeypatch.setattr(startup_selftest.sys, "executable", str(missing))
    with pytest.raises(PermissionError, match="受控普通文件"):
        startup_selftest.run_user_permission_selftest(tmp_path)

    empty = tmp_path / "PartyOps.exe"
    empty.write_bytes(b"")
    monkeypatch.setattr(startup_selftest.sys, "executable", str(empty))
    with pytest.raises(PermissionError, match="为空或不可读"):
        startup_selftest.run_user_permission_selftest(tmp_path)

    empty.write_bytes(b"MZ")
    original_read_bytes = Path.read_bytes
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda self: b"corrupted"
        if self.name == "permission.ok"
        else original_read_bytes(self),
    )
    with pytest.raises(PermissionError, match="原子写入回读失败"):
        startup_selftest.run_user_permission_selftest(tmp_path)


def test_installer_requires_original_desktop_user_runtime_probe() -> None:
    root = Path(__file__).resolve().parents[2]
    installer = (root / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )
    entrypoint = (root / "packaging" / "uos" / "entrypoint.py").read_text(
        encoding="utf-8"
    )
    assert "ExecAsOriginalUser" in installer
    assert "--startup-user-permission-self-test" in installer
    assert "PACKAGE_USER_RUNTIME_PERMISSION_SELFTEST_FAILED" in installer
    assert 'sys.argv[1:] == ["--startup-user-permission-self-test"]' in entrypoint
