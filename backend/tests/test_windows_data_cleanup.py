"""Windows 卸载时的数据所有权、预检和彻底清理回归。"""

from __future__ import annotations

import importlib.util
import json
import shlex
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "partyops_windows_data_cleanup",
    ROOT / "packaging" / "windows" / "data_cleanup.py",
)
assert SPEC and SPEC.loader
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


def _marker(path: Path, scope: str) -> None:
    path.mkdir(parents=True)
    (path / cleanup.MARKER_NAME).write_text(
        json.dumps(
            {
                "format_version": 1,
                "product": "PartyOps",
                "app_id": cleanup.APP_ID,
                "scope": scope,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (path / "partyops.db").write_bytes(b"fixture")


def _environment(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    local = tmp_path / "Users" / "tester" / "AppData" / "Local"
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Users" / "tester"))
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files x86"))
    return local, program_data


def test_user_cleanup_requires_marker_and_removes_personal_tree(monkeypatch, tmp_path: Path) -> None:
    local, _program_data = _environment(monkeypatch, tmp_path)
    config = local / "PartyOps"
    config.mkdir(parents=True)
    personal = tmp_path / "Data" / "个人 数据"
    _marker(personal, "personal")
    (config / "personal.env").write_text(
        f"PARTYOPS_DATA_DIR={shlex.quote(str(personal))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cleanup, "_stop_owned_user_processes", lambda: None)
    monkeypatch.setattr(cleanup, "_remove_user_ca", lambda: None)
    cleanup.execute("user", check_only=True)
    assert personal.exists() and config.exists()
    cleanup.execute("user", check_only=False)
    assert not personal.exists() and not config.exists()

    config.mkdir(parents=True)
    unsafe = tmp_path / "Data" / "没有标记"
    unsafe.mkdir(parents=True)
    (config / "personal.env").write_text(
        f"PARTYOPS_DATA_DIR={shlex.quote(str(unsafe))}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="缺少 PartyOps 所有权标记"):
        cleanup.execute("user", check_only=True)
    assert unsafe.exists()


def test_system_cleanup_accepts_marked_programdata_child_and_rejects_wrong_scope(
    monkeypatch, tmp_path: Path
) -> None:
    _local, program_data = _environment(monkeypatch, tmp_path)
    control = program_data / "PartyOps"
    control.mkdir(parents=True)
    host = program_data / "PartyOps-Data"
    _marker(host, "host")
    (control / "partyops.env").write_text(
        f"PARTYOPS_DATA_DIR={shlex.quote(str(host))}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cleanup, "_stop_system_services", lambda: None)
    cleanup.execute("system", check_only=True)

    marker = json.loads((host / cleanup.MARKER_NAME).read_text(encoding="utf-8"))
    marker["scope"] = "personal"
    (host / cleanup.MARKER_NAME).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError, match="所有权标记不匹配"):
        cleanup.execute("system", check_only=True)


def test_installer_exposes_preserve_or_full_delete_choice() -> None:
    installer = (ROOT / "packaging" / "windows" / "PartyOps.iss").read_text(
        encoding="utf-8"
    )
    build = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    assert "PartyOpsDataCleanup" in build
    assert "function InitializeUninstall" in installer
    assert "彻底卸载" in installer and "仅删除程序" in installer
    assert "ExecAsOriginalUser(" not in installer
    assert "RunDataCleanup('runtime', False)" in installer
    assert "UNINSTALL_DATA_PREFLIGHT_FAILED" in installer


def test_runtime_cleanup_removes_only_system_cache_and_preserves_business_data(
    monkeypatch, tmp_path: Path
) -> None:
    _local, program_data = _environment(monkeypatch, tmp_path)
    system_cache = program_data / "PartyOps-System"
    (system_cache / "installer-cache").mkdir(parents=True)
    (system_cache / "installer-cache" / "current.exe").write_bytes(b"installer")
    business_control = program_data / "PartyOps"
    business_control.mkdir(parents=True)
    (business_control / "partyops.env").write_text(
        "PARTYOPS_MODE=host\n", encoding="utf-8"
    )
    events: list[str] = []
    monkeypatch.setattr(
        cleanup, "_stop_owned_user_processes", lambda: events.append("user")
    )
    monkeypatch.setattr(
        cleanup, "_remove_owned_autostarts", lambda: events.append("autostart")
    )
    monkeypatch.setattr(
        cleanup, "_stop_system_services", lambda: events.append("services")
    )

    cleanup.execute("runtime", check_only=False)

    assert events == ["user", "autostart", "services"]
    assert not system_cache.exists()
    assert business_control.exists()


def test_windows_helpers_share_one_verified_runtime_instead_of_embedding_duplicates() -> None:
    build = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(
        encoding="utf-8"
    )
    entries = (
        "PartyOps",
        "PartyOpsAgent",
        "PartyOpsWizard",
        "PartyOpsUpdater",
        "PartyOpsLauncher",
        "PartyOpsDataCleanup",
        "PartyOpsFileOpen",
        "PartyOpsService",
        "PartyOpsUpdaterService",
    )
    for name in entries:
        assert f'Name = "{name}"' in build
    assert build.count('Mode = "onedir"') == len(entries)
    assert 'Copy-Item -Path (Join-Path $buildRoot "$($entry.Name)\\*")' in build

    package = (ROOT / "packaging" / "windows" / "package-windows.ps1").read_text(
        encoding="utf-8"
    )
    assert '"PartyOpsDataCleanup"' in package
    assert 'Join-Path $runtimeRoot "$entry\\$entry.exe"' in package
    assert 'Copy-Item -Path (Join-Path $runtimeRoot "$entry\\*")' in package
    assert 'Join-Path $runtimeRoot "$entry.exe"' not in package
