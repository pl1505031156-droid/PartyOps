"""个人模式权限预检与桌面账号安装探针回归。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app import setup_wizard, startup_selftest
from app.windows_host_status import RUNTIME_PERMISSION_DENIED


def _write_win7_runtime_fixture(root: Path, *, platform: str = "windows7") -> Path:
    """生成最小但哈希闭合的 Win7 冻结运行时清单。"""

    internal = root / "_internal"
    internal.mkdir(parents=True)
    ucrt = {"files": {"ucrtbase.dll": "source-only"}}
    vc_files = {
        "vcruntime140.dll": "source-only",
        "msvcp140.dll": "source-only",
    }
    if setup_wizard.sys.maxsize > 2**32:
        vc_files["vcruntime140_1.dll"] = "source-only"
    vc = {"files": vc_files}
    (root / "ucrt-source.json").write_text(json.dumps(ucrt), encoding="utf-8")
    (root / "vc-runtime-source.json").write_text(json.dumps(vc), encoding="utf-8")
    paths = [
        "ucrt-source.json",
        "vc-runtime-source.json",
        "PartyOps.exe",
        "PartyOpsLauncher.exe",
        "PartyOpsWizard.exe",
        "ucrtbase.dll",
        "_internal/ucrtbase.dll",
        "vcruntime140.dll",
        "_internal/vcruntime140.dll",
        "msvcp140.dll",
        "_internal/msvcp140.dll",
        "sqlite3.dll",
        "_internal/sqlite3.dll",
        "_internal/_sqlite3.pyd",
        "_internal/python3.dll",
        "_internal/python38.dll",
        "_internal/_tkinter.pyd",
        "_internal/tcl86t.dll",
        "_internal/tk86t.dll",
        "_internal/_tcl_data/init.tcl",
        "_internal/_tk_data/tk.tcl",
        "_internal/frontend/index.html",
    ]
    if setup_wizard.sys.maxsize > 2**32:
        paths.extend(
            [
                "vcruntime140_1.dll",
                "_internal/vcruntime140_1.dll",
            ]
        )
    for relative in paths[2:]:
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("payload:" + relative).encode("utf-8"))
    files = []
    for relative in paths:
        path = root / Path(relative)
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "platform": platform,
                "architecture": "amd64" if setup_wizard.sys.maxsize > 2**32 else "x86",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return root / "PartyOps.exe"


def _write_windows_runtime_fixture(
    root: Path,
    *,
    platform: str = "windows",
    uppercase_vc_paths: bool = False,
) -> Path:
    """生成可供 Win10/11 通用包预检的最小哈希闭包。"""

    internal = root / "_internal"
    internal.mkdir(parents=True)
    vc_names = (
        "VCRUNTIME140.dll" if uppercase_vc_paths else "vcruntime140.dll",
        "VCRUNTIME140_1.dll" if uppercase_vc_paths else "vcruntime140_1.dll",
        "MSVCP140.dll" if uppercase_vc_paths else "msvcp140.dll",
    )
    paths = [
        "PartyOps.exe",
        "PartyOpsLauncher.exe",
        "PartyOpsWizard.exe",
        "sqlite3.dll",
        "_internal/sqlite3.dll",
        "_internal/_sqlite3.pyd",
        "_internal/python3.dll",
        f"_internal/python{setup_wizard.sys.version_info.major}{setup_wizard.sys.version_info.minor}.dll",
        "_internal/ucrtbase.dll",
        "_internal/_tkinter.pyd",
        "_internal/tcl86t.dll",
        "_internal/tk86t.dll",
        "_internal/_tcl_data/init.tcl",
        "_internal/_tk_data/tk.tcl",
        "_internal/frontend/index.html",
        *(f"_internal/{name}" for name in vc_names),
    ]
    for relative in paths:
        path = root / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("payload:" + relative).encode("utf-8"))
    files = [
        {
            "path": relative,
            "sha256": hashlib.sha256((root / Path(relative)).read_bytes()).hexdigest(),
        }
        for relative in paths
    ]
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "platform": platform,
                "architecture": "amd64" if setup_wizard.sys.maxsize > 2**32 else "x86",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return root / "PartyOps.exe"


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
    assert "--startup-desktop-user-self-test" in installer
    assert "PACKAGE_DESKTOP_RUNTIME_STARTUP_SELFTEST_FAILED" in installer
    assert 'sys.argv[1:] == ["--startup-desktop-user-self-test"]' in entrypoint
    assert 'sys.argv[1:] == ["--startup-user-permission-self-test"]' in entrypoint


def test_win7_runtime_dependency_preflight_accepts_complete_hashed_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _write_win7_runtime_fixture(tmp_path)
    monkeypatch.setattr(setup_wizard, "_missing_win7_loader_apis", lambda: [])
    setup_wizard._preflight_windows_runtime_dependencies(
        executable,
        force=True,
        windows_version=(6, 1),
    )


def test_win7_runtime_dependency_preflight_distinguishes_wrong_package_and_os_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _write_win7_runtime_fixture(tmp_path, platform="windows")
    with pytest.raises(setup_wizard.HostStartupError) as wrong_package:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(6, 1),
        )
    assert wrong_package.value.code == "RUNTIME_PACKAGE_MISMATCH"
    assert "windows7" in str(wrong_package.value)

    manifest = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    manifest["platform"] = "windows7"
    (tmp_path / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard,
        "_missing_win7_loader_apis",
        lambda: ["AddDllDirectory"],
    )
    with pytest.raises(setup_wizard.HostStartupError) as missing_update:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(6, 1),
        )
    assert missing_update.value.code == "RUNTIME_SYSTEM_UPDATE_REQUIRED"
    assert "KB2533623" in str(missing_update.value)


def test_win7_runtime_dependency_preflight_names_missing_or_changed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _write_win7_runtime_fixture(tmp_path)
    monkeypatch.setattr(setup_wizard, "_missing_win7_loader_apis", lambda: [])
    (tmp_path / "_internal" / "vcruntime140.dll").unlink()
    (tmp_path / "ucrtbase.dll").write_bytes(b"tampered")
    with pytest.raises(setup_wizard.HostStartupError) as incomplete:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(6, 1),
        )
    assert incomplete.value.code == "RUNTIME_DEPENDENCY_MISSING"
    assert "_internal/vcruntime140.dll" in incomplete.value.detail
    assert "ucrtbase.dll" in incomplete.value.detail


def test_win7_runtime_dependency_preflight_rejects_missing_manifest_and_wrong_architecture(
    tmp_path: Path
) -> None:
    executable = tmp_path / "PartyOps.exe"
    executable.write_bytes(b"MZ")
    with pytest.raises(setup_wizard.HostStartupError) as missing_manifest:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(6, 1),
        )
    assert missing_manifest.value.code == "RUNTIME_DEPENDENCY_MISSING"

    executable = _write_win7_runtime_fixture(tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["architecture"] = "x86" if setup_wizard.sys.maxsize > 2**32 else "amd64"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(setup_wizard.HostStartupError) as wrong_arch:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(6, 1),
        )
    assert wrong_arch.value.code == "RUNTIME_BINARY_INCOMPATIBLE"


@pytest.mark.parametrize("windows_version", [(10, 0), (11, 0)])
def test_modern_windows_runtime_preflight_verifies_complete_hashed_dependency_closure(
    tmp_path: Path,
    windows_version: tuple[int, int],
) -> None:
    executable = _write_windows_runtime_fixture(
        tmp_path,
        uppercase_vc_paths=True,
    )
    setup_wizard._preflight_windows_runtime_dependencies(
        executable,
        force=True,
        windows_version=windows_version,
    )


@pytest.mark.parametrize(
    ("relative", "expected_detail"),
    [
        ("_internal/python3.dll", "_internal/python3.dll"),
        ("_internal/_sqlite3.pyd", "_internal/_sqlite3.pyd"),
        ("_internal/vcruntime140.dll", "_internal/VCRUNTIME140.dll"),
        ("_internal/ucrtbase.dll", "_internal/ucrtbase.dll"),
    ],
)
def test_modern_windows_runtime_preflight_rejects_missing_or_tampered_dependencies(
    tmp_path: Path,
    relative: str,
    expected_detail: str,
) -> None:
    executable = _write_windows_runtime_fixture(
        tmp_path,
        uppercase_vc_paths=True,
    )
    manifest = json.loads((tmp_path / "release-manifest.json").read_text(encoding="utf-8"))
    manifest_entry = next(
        item for item in manifest["files"] if item["path"].lower() == relative.lower()
    )
    target = tmp_path / Path(manifest_entry["path"])
    if relative.endswith("python3.dll") or relative.endswith("_sqlite3.pyd"):
        target.unlink()
    else:
        target.write_bytes(b"tampered")
    with pytest.raises(setup_wizard.HostStartupError) as incomplete:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(10, 0),
        )
    assert incomplete.value.code == "RUNTIME_DEPENDENCY_MISSING"
    assert expected_detail in incomplete.value.detail


def test_modern_windows_runtime_preflight_rejects_wrong_versioned_python(
    tmp_path: Path,
) -> None:
    executable = _write_windows_runtime_fixture(tmp_path)
    expected = (
        tmp_path
        / "_internal"
        / f"python{setup_wizard.sys.version_info.major}{setup_wizard.sys.version_info.minor}.dll"
    )
    unexpected = expected.with_name("python399.dll")
    expected.replace(unexpected)
    manifest_path = tmp_path / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if item["path"].endswith(expected.name):
            item["path"] = "_internal/python399.dll"
            item["sha256"] = hashlib.sha256(unexpected.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(setup_wizard.HostStartupError) as mismatch:
        setup_wizard._preflight_windows_runtime_dependencies(
            executable,
            force=True,
            windows_version=(10, 0),
        )
    assert mismatch.value.code == "RUNTIME_PACKAGE_MISMATCH"
    assert "Python" in str(mismatch.value)
