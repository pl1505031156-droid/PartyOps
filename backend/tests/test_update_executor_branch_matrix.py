"""更新执行器在锁、平台选择、安装失败与监督器分支上的回归。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import update_executor
from app.enums import UpdateStatus


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    transfers = data_dir / "transfers"
    updates = data_dir / "updates"
    transfers.mkdir(parents=True)
    updates.mkdir()
    return SimpleNamespace(
        data_dir=data_dir,
        transfers_dir=transfers,
        updates_dir=updates,
        database_path=data_dir / "partyops.db",
        attachments_dir=data_dir / "attachments",
        archives_dir=data_dir / "archives",
        update_public_key="",
        tls_enabled=False,
        tls_client_ca_file=None,
        host="127.0.0.1",
        port=18765,
    )


def _package(path: Path, manifest: dict, artifact_name: str, payload: bytes = b"artifact") -> None:
    manifest = dict(manifest)
    manifest.setdefault(
        "artifacts",
        {
            artifact_name: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(artifact_name, payload)


def test_lock_signature_architecture_and_platform_guards(monkeypatch, tmp_path: Path) -> None:
    os_proxy = SimpleNamespace(**vars(os))
    monkeypatch.setattr(update_executor, "os", os_proxy)
    lock = tmp_path / ".update.lock"
    lock.write_text("{", encoding="utf-8")
    monkeypatch.setattr(update_executor.time, "time", lambda: 1_000.0)
    os.utime(lock, (900, 900))
    assert not update_executor._update_lock_is_stale(lock)
    os.utime(lock, (0, 0))
    assert update_executor._update_lock_is_stale(lock)

    lock.write_text(json.dumps({"pid": os.getpid(), "boot_id": "old-boot"}), encoding="utf-8")
    monkeypatch.setattr(update_executor, "_system_boot_id", lambda: "new-boot")
    assert update_executor._update_lock_is_stale(lock)

    target = tmp_path / "write-failure.lock"
    monkeypatch.setattr(update_executor.os, "write", lambda *_a: (_ for _ in ()).throw(OSError("disk full")))
    assert not update_executor._acquire_update_lock(target)
    assert not target.exists()

    monkeypatch.setattr(update_executor, "get_settings", lambda: SimpleNamespace(update_public_key=""))
    monkeypatch.setattr(update_executor.os, "name", "nt")
    monkeypatch.setattr(update_executor.sys, "executable", str(tmp_path / "runtime" / "PartyOps.exe"))
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    assert update_executor._trusted_public_key() == ""
    assert not update_executor._verify_manifest_signature({})

    monkeypatch.setattr(os_proxy, "name", "posix")
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "arm64")
    assert update_executor._architecture() == "arm64"
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "riscv64")
    with pytest.raises(RuntimeError, match="支持范围"):
        update_executor._architecture()

    assert not update_executor._manifest_has_windows_artifact({"platform_artifacts": []})
    assert not update_executor._manifest_has_windows_artifact({"platform_artifacts": {"windows": []}})
    assert not update_executor._manifest_has_windows_artifact({"platform_artifacts": {"windows": {"arm64": "x.exe"}}})


def test_windows_update_lock_and_unrecoverable_failure(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    package = tmp_path / "release.partyops-update"
    package.write_bytes(b"package")
    states = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor,
        "_windows_installer_cache",
        lambda: settings.data_dir / "installer-cache",
    )
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    assert not update_executor._execute_windows_host_update("locked", package, {})

    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda path: path.write_text("lock", encoding="utf-8") or True)
    monkeypatch.setattr(update_executor, "_select_artifact", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("invalid")))
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_k: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, **kwargs: states.append(kwargs))
    monkeypatch.setattr(update_executor, "_restore_managed_tree", lambda *_a: None)
    assert not update_executor._execute_windows_host_update("no-rollback", package, {})
    assert states[-1]["status"] == UpdateStatus.FAILED
    assert "原版本保持不变" in states[-1]["message"]
    assert commands == []
    assert not (settings.data_dir / ".update.lock").exists()


@pytest.mark.parametrize(
    ("dpkg_ready", "unpack_code", "configure_code", "expected"),
    [
        (False, 0, 0, False),
        (True, 0, 0, True),
        (True, 0, 1, False),
    ],
)
def test_uos_device_install_matrix(monkeypatch, tmp_path: Path, dpkg_ready: bool, unpack_code: int, configure_code: int, expected: bool) -> None:
    settings = _settings(tmp_path)
    artifact_name = "partyops_1.4.3_amd64.deb"
    package = tmp_path / "partyops_1.4.3.partyops-update"
    manifest = {
        "version": "1.4.3",
        "architecture_artifacts": {"amd64": artifact_name},
    }
    _package(package, manifest, artifact_name)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_manifest_has_windows_artifact", lambda *_a, **_k: False)
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: "linux-deb")
    monkeypatch.setattr(update_executor, "_select_artifact", lambda _p, _m, _a, target, _platform: target.write_bytes(b"deb") or target)
    installed_versions = iter(["1.4.2-1", "1.4.3"])
    monkeypatch.setattr(
        update_executor,
        "_installed_package_version",
        lambda: next(installed_versions, "1.4.2-1"),
    )
    monkeypatch.setattr(
        update_executor,
        "_create_installed_package_snapshot",
        lambda target: target.write_bytes(b"rollback-deb"),
    )
    monkeypatch.setattr(update_executor, "_ensure_dpkg_ready", lambda: dpkg_ready)

    def run(command, **_kwargs):
        code = unpack_code if "--unpack" in command else configure_code
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(update_executor, "_run", run)
    assert update_executor.install_device_package(package) is expected
    assert not update_executor.install_device_package(tmp_path / "not-update.zip")


def test_windows_device_install_and_supervisor_matrix(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path / "windows")
    artifact_name = "PartyOps_1.4.3_windows_amd64.exe"
    package = tmp_path / "windows.partyops-update"
    manifest = {
        "version": "9.9.9",
        "platform_artifacts": {"windows": {"amd64": artifact_name}},
    }
    _package(package, manifest, artifact_name)
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor.os, "name", "nt")
    monkeypatch.setattr(update_executor, "_manifest_has_windows_artifact", lambda *_a, **_k: True)
    monkeypatch.setattr(update_executor, "_select_artifact", lambda _p, _m, _a, target, _platform: target.write_bytes(b"exe") or target)
    installed = []
    monkeypatch.setattr(update_executor, "_run_windows_installer", lambda path: installed.append(path) or True)
    assert update_executor.install_device_package(package)
    assert installed

    data = tmp_path / "supervisor"
    data.mkdir()
    launched = []
    monkeypatch.setattr(update_executor, "_candidate_host_environments", lambda: [
        {"PARTYOPS_DATA_DIR": str(data)},
        {"PARTYOPS_DATA_DIR": str(data)},
    ])
    pending = iter([None, "run-live"])
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: next(pending))
    lock = data / ".update.lock"
    lock.write_text("live", encoding="utf-8")
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: False)
    monkeypatch.setattr(update_executor.subprocess, "Popen", lambda command, **_kwargs: launched.append(command))
    assert update_executor.run_supervisor(once=True) == 0
    assert not launched

    lock.unlink()
    monkeypatch.setattr(update_executor, "_candidate_host_environments", lambda: [{"PARTYOPS_DATA_DIR": str(data)}])
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: "run-frozen")
    monkeypatch.setattr(update_executor.sys, "frozen", True, raising=False)
    assert update_executor.run_supervisor(once=True) == 0
    assert launched[-1] == [sys.executable, "--run-id", "run-frozen"]


def test_process_lock_and_public_key_edge_matrix(monkeypatch, tmp_path: Path) -> None:
    os_proxy = SimpleNamespace(**vars(os))
    os_proxy.name = "posix"
    monkeypatch.setattr(update_executor, "os", os_proxy)

    assert not update_executor._process_is_running(0)
    monkeypatch.setattr(os_proxy, "kill", lambda *_a: None)
    assert update_executor._process_is_running(42)
    monkeypatch.setattr(
        os_proxy,
        "kill",
        lambda *_a: (_ for _ in ()).throw(ProcessLookupError()),
    )
    assert not update_executor._process_is_running(42)
    monkeypatch.setattr(
        os_proxy,
        "kill",
        lambda *_a: (_ for _ in ()).throw(PermissionError()),
    )
    assert update_executor._process_is_running(42)
    monkeypatch.setattr(
        os_proxy,
        "kill",
        lambda *_a: (_ for _ in ()).throw(OSError()),
    )
    assert not update_executor._process_is_running(42)

    missing = tmp_path / "missing.lock"
    assert update_executor._update_lock_is_stale(missing)
    live = tmp_path / "live.lock"
    live.write_text(json.dumps({"pid": 42, "boot_id": "same"}), encoding="utf-8")
    monkeypatch.setattr(update_executor, "_system_boot_id", lambda: "same")
    monkeypatch.setattr(update_executor, "_process_is_running", lambda _pid: True)
    assert not update_executor._update_lock_is_stale(live)
    assert not update_executor._acquire_update_lock(live)
    monkeypatch.setattr(update_executor, "_process_is_running", lambda _pid: False)
    assert update_executor._acquire_update_lock(live)
    assert json.loads(live.read_text(encoding="utf-8"))["pid"] == os.getpid()

    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(update_public_key=" configured-key "),
    )
    assert update_executor._trusted_public_key() == "configured-key"


def test_in_app_package_manager_marks_transaction(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(update_executor, "_run", run)
    result = update_executor._run_linux_package_manager(
        ["dpkg", "--configure", "partyops"], timeout=300
    )
    assert result.returncode == 0
    assert calls == [
        (
            ["dpkg", "--configure", "partyops"],
            {
                "timeout": 300,
                "environment": {"PARTYOPS_IN_APP_UPDATE": "1"},
            },
        )
    ]


def test_package_manager_health_and_platform_edge_matrix(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1 if command[0] == "dpkg-query" else 0,
            "1.4.3-0.rc.3.1" if command[0] == "rpm" else "",
            "",
        ),
    )
    assert update_executor._installed_package_version() == ""
    assert update_executor._installed_rpm_version() == "1.4.3-0.rc.3.1"
    monkeypatch.setattr(update_executor.shutil, "which", lambda _name: None)
    assert not update_executor._install_rpm(tmp_path / "partyops.rpm")
    monkeypatch.setattr(
        update_executor.shutil,
        "which",
        lambda name: "/usr/bin/dnf" if name == "dnf" else None,
    )
    assert update_executor._install_rpm(tmp_path / "partyops.rpm")

    settings = _settings(tmp_path / "health")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)

    class Response:
        status = 200

        def read(self, _size=-1):
            return (
                b'{"status":"ok","mode":"host","app_version":"1.4.3-rc.3",'
                b'"sqlite":{"safe_version":true,"fts5":true}}'
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(update_executor.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert update_executor._health_check()
    monkeypatch.setattr(
        update_executor.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    assert not update_executor._health_check()

    monkeypatch.setattr(
        update_executor,
        "detect_platform_info",
        lambda: SimpleNamespace(platform_family="unsupported", package_format="unknown"),
    )
    monkeypatch.setattr(update_executor, "update_platform_key", lambda _info: "")
    with pytest.raises(RuntimeError, match="无法匹配"):
        update_executor._manifest_platform_name({"format_version": 3})


def test_environment_parser_ignores_invalid_and_preserves_safe_values(tmp_path: Path) -> None:
    environment = tmp_path / "partyops.env"
    environment.write_text(
        "\n".join(
            [
                "# 注释",
                "IGNORED=value",
                "PARTYOPS_MODE=host",
                "PARTYOPS_DATA_DIR='/data/PartyOps 资料'",
                "PARTYOPS_BAD='unterminated",
            ]
        ),
        encoding="utf-8",
    )
    assert update_executor._read_environment(tmp_path / "missing.env") == {}
    values = update_executor._read_environment(environment)
    assert values == {
        "PARTYOPS_MODE": "host",
        "PARTYOPS_DATA_DIR": "/data/PartyOps 资料",
    }
