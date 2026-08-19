from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import __version__ as APP_VERSION
from app import update_executor
from app.enums import UpdateStatus
from app.models import UpdatePackage, UpdateRun


class _Session:
    def __init__(self, *, scalar_value=None) -> None:
        self.scalar_value = scalar_value
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _model, _identity):
        return None

    def scalar(self, _query):
        return self.scalar_value

    def commit(self):
        self.committed = True


def _settings(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    transfers = data_dir / "transfers"
    transfers.mkdir()
    attachments = data_dir / "attachments"
    archives = data_dir / "archives"
    attachments.mkdir()
    archives.mkdir()
    (attachments / "a.txt").write_text("attachment", encoding="utf-8")
    (archives / "a.txt").write_text("archive", encoding="utf-8")
    database = data_dir / "partyops.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample(value) VALUES ('ok')")
    return SimpleNamespace(
        data_dir=data_dir,
        database_path=database,
        attachments_dir=attachments,
        archives_dir=archives,
        transfers_dir=transfers,
        updates_dir=data_dir / "updates",
        update_public_key="public-key",
        tls_enabled=False,
        tls_client_ca_file=None,
        host="127.0.0.1",
        port=18765,
    )


def test_process_lock_environment_and_health_helpers(monkeypatch, tmp_path: Path) -> None:
    assert not update_executor._process_is_running(0)
    assert update_executor._process_is_running(os.getpid())
    assert not update_executor._process_is_running(999_999_999)

    missing = tmp_path / "missing.lock"
    assert update_executor._update_lock_is_stale(missing)
    lock = tmp_path / "update.lock"
    assert update_executor._acquire_update_lock(lock)
    assert not update_executor._acquire_update_lock(lock)
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    lock.write_text(json.dumps({"pid": 999_999_999, "boot_id": update_executor._system_boot_id()}), encoding="utf-8")
    assert update_executor._update_lock_is_stale(lock)
    assert update_executor._acquire_update_lock(lock)

    environment = tmp_path / "partyops.env"
    environment.write_text(
        "# 注释\nPARTYOPS_MODE=host\nPARTYOPS_DATA_DIR='/var/lib/partyops'\nINVALID=x\nPARTYOPS_BAD='unterminated\n",
        encoding="utf-8",
    )
    values = update_executor._read_environment(environment)
    assert values == {"PARTYOPS_MODE": "host", "PARTYOPS_DATA_DIR": "/var/lib/partyops"}
    assert update_executor._read_environment(tmp_path / "none.env") == {}

    data_dir = tmp_path / "pending"
    data_dir.mkdir()
    assert update_executor._pending_run_id(data_dir) is None
    database = data_dir / "partyops.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE update_runs (id TEXT, target_device_id TEXT, status TEXT, created_at TEXT)")
        connection.execute("INSERT INTO update_runs VALUES ('run-1', NULL, 'APPLYING', '2026-08-11')")
    assert update_executor._pending_run_id(data_dir) == "run-1"

    settings = _settings(tmp_path / "health")
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.urllib.request, "urlopen", lambda *_a, **_k: SimpleNamespace(__enter__=lambda self: self, __exit__=lambda *_args: False, status=200))
    # SimpleNamespace 的特殊方法不参与协议查找，因此用明确响应对象。
    class Response:
        status = 200

        def read(self, _size=-1):
            return (
                f'{{"status":"ok","mode":"host","app_version":"{APP_VERSION}",'
                '"sqlite":{"safe_version":true,"fts5":true}}'
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(update_executor.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert update_executor._health_check()
    assert update_executor._manifest_has_windows_artifact({"platform_artifacts": {"windows": {"amd64": "PartyOps.exe"}}})
    assert not update_executor._manifest_has_windows_artifact({})

    installer_commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, timeout=120: installer_commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert update_executor._run_windows_installer(tmp_path / "PartyOps.exe")
    assert "/INAPPUPDATE=1" not in installer_commands[-1]
    assert update_executor._run_windows_installer(
        tmp_path / "PartyOps.exe", service_handoff=True
    )
    assert installer_commands[-1][-1] == "/INAPPUPDATE=1"
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, timeout=120: subprocess.CompletedProcess(
            command, 1056, "已经运行", ""
        ),
    )
    assert update_executor._start_windows_host_service_after_update()
    monkeypatch.setattr(update_executor, "get_settings", lambda: SimpleNamespace(update_public_key="fixed-key"))
    assert update_executor._trusted_public_key() == "fixed-key"


def test_windows_host_update_success_and_rollback(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path / "success")
    package_path = tmp_path / "release.partyops-update"
    package_path.write_bytes(b"package")
    cache = settings.data_dir / "installer-cache"
    cache.mkdir()
    (cache / "current.exe").write_bytes(b"old-installer")
    (cache / "current.exe.sha256").write_text(
        update_executor._hash(cache / "current.exe"), encoding="ascii"
    )
    states: list[tuple[UpdateStatus, int, str]] = []
    monkeypatch.setattr(update_executor, "_windows_installer_cache", lambda: cache)

    def select_artifact(_package, _manifest, _architecture, target, _platform):
        target.write_bytes(b"new-installer")
        return target

    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "_select_artifact", select_artifact)
    handoffs: list[bool] = []

    def install_and_seed(path: Path, **kwargs) -> bool:
        handoffs.append(bool(kwargs.get("service_handoff")))
        update_executor._cache_verified_rollback_artifact(path, cache / "current.exe")
        return True

    monkeypatch.setattr(update_executor, "_run_windows_installer", install_and_seed)
    monkeypatch.setattr(update_executor, "_health_check", lambda *_args: True)
    monkeypatch.setattr(update_executor, "_run", lambda command, timeout=120: subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, *, status, progress, message: states.append((status, progress, message)))
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: _Session())
    assert update_executor._execute_windows_host_update("run-success", package_path, {})
    assert handoffs == [True]
    assert (cache / "current.exe").read_bytes() == b"new-installer"
    assert any(progress == 80 for _, progress, _ in states)
    assert not (settings.data_dir / ".update.lock").exists()

    failure_settings = _settings(tmp_path / "failure")
    failure_cache = failure_settings.data_dir / "installer-cache"
    failure_cache.mkdir()
    (failure_cache / "current.exe").write_bytes(b"rollback-installer")
    (failure_cache / "current.exe.sha256").write_text(
        update_executor._hash(failure_cache / "current.exe"), encoding="ascii"
    )
    installer_results = iter([False, True])
    failure_states: list[UpdateStatus] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: failure_settings)
    monkeypatch.setattr(update_executor, "_windows_installer_cache", lambda: failure_cache)
    monkeypatch.setattr(
        update_executor,
        "_restore_database_snapshot",
        lambda source, destination: destination.write_bytes(source.read_bytes()),
    )
    monkeypatch.setattr(
        update_executor,
        "_run_windows_installer",
        lambda _path, **_kwargs: next(installer_results),
    )
    monkeypatch.setattr(update_executor, "_set_run", lambda _id, *, status, progress, message: failure_states.append(status))
    assert not update_executor._execute_windows_host_update("run-failure", package_path, {})
    assert failure_states[-1] == UpdateStatus.ROLLED_BACK
    assert (failure_cache / "current.exe").read_bytes() == b"rollback-installer"


def test_online_backup_restore_daemon_supervisor_and_main(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")
    update_executor._online_backup_database(source, destination)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "ok"

    data_root = tmp_path / "data"
    backup = tmp_path / "backup"
    destination_tree = data_root / "attachments"
    backup.mkdir()
    (backup / "a.txt").write_text("restored", encoding="utf-8")
    destination_tree.mkdir(parents=True)
    (destination_tree / "old.txt").write_text("old", encoding="utf-8")
    update_executor._restore_managed_tree(backup, destination_tree, data_root)
    assert (destination_tree / "a.txt").read_text(encoding="utf-8") == "restored"
    with pytest.raises(RuntimeError, match="超出"):
        update_executor._restore_managed_tree(backup, tmp_path / "outside", data_root)

    run = SimpleNamespace(id="run-1")
    executed: list[str] = []
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: _Session(scalar_value=run))
    monkeypatch.setattr(update_executor, "execute_host_update", lambda run_id: executed.append(run_id) or True)
    assert update_executor.run_daemon(once=True) == 0
    assert executed == ["run-1"]

    class FailingFactory:
        def __call__(self):
            raise OperationalError("SELECT 1", {}, Exception("locked"))

    monkeypatch.setattr(update_executor.db_runtime, "session_factory", FailingFactory())
    assert update_executor.run_daemon(once=True) == 0

    supervisor_data = tmp_path / "supervisor"
    supervisor_data.mkdir()
    launched: list[list[str]] = []
    monkeypatch.setattr(update_executor, "_candidate_host_environments", lambda: [{"PARTYOPS_DATA_DIR": str(supervisor_data)}])
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: "run-supervisor")
    monkeypatch.setattr(update_executor.subprocess, "Popen", lambda command, **_kwargs: launched.append(command))
    assert update_executor.run_supervisor(once=True) == 0
    assert launched and "run-supervisor" in launched[0]

    monkeypatch.setattr(update_executor, "run_daemon", lambda once=False: 0)
    monkeypatch.setattr(sys, "argv", ["partyops-update-executor", "--once"])
    with pytest.raises(SystemExit) as exit_info:
        update_executor.main()
    assert exit_info.value.code == 0


def test_windows_process_architecture_key_and_installer_branches(monkeypatch, tmp_path: Path) -> None:
    import ctypes

    closed: list[int] = []

    class Kernel:
        @staticmethod
        def OpenProcess(_access, _inherit, pid):
            return 88 if pid == 42 else 0

        @staticmethod
        def CloseHandle(handle):
            closed.append(handle)

    monkeypatch.setattr(update_executor.os, "name", "nt")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=Kernel()), raising=False)
    assert update_executor._process_is_running(42)
    assert not update_executor._process_is_running(43)
    assert closed == [88]
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "AMD64")
    assert update_executor._architecture() == "amd64"
    monkeypatch.setattr(update_executor.platform, "machine", lambda: "arm64")
    with pytest.raises(RuntimeError, match="当前系统架构不在 PartyOps 支持范围"):
        update_executor._architecture()

    runtime = tmp_path / "PartyOps.exe"
    runtime.write_bytes(b"runtime")
    key = tmp_path / "update-public-key.txt"
    key.write_text("  file-public-key  ", encoding="utf-8")
    monkeypatch.setattr(update_executor.sys, "executable", str(runtime))
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(update_public_key=""),
    )
    assert update_executor._trusted_public_key() == "file-public-key"
    monkeypatch.setattr(update_executor, "_trusted_public_key", lambda: "invalid-base64")
    assert not update_executor._verify_manifest_signature({"signature": "also-invalid"})


def test_artifact_manifest_version_snapshot_and_queue_guards(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _manifest: True)
    package = tmp_path / "release.partyops-update"
    name = "partyops_1.4.2_amd64.deb"
    payload = b"signed-deb"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(name, payload)

    base = {
        "version": "1.4.3-rc.8",
        "architecture_artifacts": {"amd64": name},
        "artifacts": {
            name: {
                "size": len(payload),
                "sha256": update_executor.hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    inconsistent = {**base, "artifacts": {"bad.exe": {"size": 1, "sha256": "0" * 64}}}
    with pytest.raises(RuntimeError, match="清单不一致"):
        update_executor._select_artifact(
            package, inconsistent, "amd64", tmp_path / "bad.deb"
        )
    wrong_size = json.loads(json.dumps(base))
    wrong_size["artifacts"][name]["size"] = 999
    with pytest.raises(RuntimeError, match="大小"):
        update_executor._select_artifact(
            package, wrong_size, "amd64", tmp_path / "size.deb"
        )
    wrong_hash = json.loads(json.dumps(base))
    wrong_hash["artifacts"][name]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="哈希"):
        update_executor._select_artifact(
            package, wrong_hash, "amd64", tmp_path / "hash.deb"
        )

    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", ""),
            subprocess.CompletedProcess([], 0, "deinstall ok config-files\t1.4.1", ""),
            subprocess.CompletedProcess([], 0, "install ok installed\t1.4.2", ""),
        ]
    )
    monkeypatch.setattr(update_executor, "_run", lambda *_args, **_kwargs: next(results))
    assert update_executor._installed_package_version() == ""
    assert update_executor._installed_package_version() == ""
    assert update_executor._installed_package_version() == "1.4.2"

    rollback = tmp_path / "rollback.deb"
    monkeypatch.setattr(update_executor, "_installed_package_version", lambda: "")
    with pytest.raises(RuntimeError, match="系统包版本"):
        update_executor._create_installed_package_snapshot(rollback)
    monkeypatch.setattr(update_executor, "_installed_package_version", lambda: "1.4.1")
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, "", "failed"),
    )
    with pytest.raises(RuntimeError, match="文件清单"):
        update_executor._create_installed_package_snapshot(rollback)

    runs = [
        SimpleNamespace(target_device_id="device-1", message=""),
        SimpleNamespace(target_device_id="device-2", message=""),
    ]

    class Rows:
        def all(self):
            return runs

    class QueueDb:
        def scalars(self, _query):
            return Rows()

    count = update_executor._queue_device_updates(SimpleNamespace(id="package-1"), QueueDb())
    assert count == 2 and all("等待协同电脑" in item.message for item in runs)


def test_set_run_health_pending_and_supervisor_defensive_paths(monkeypatch, tmp_path: Path) -> None:
    run = SimpleNamespace(status=None, progress=0, message="", completed_at=None)

    class RunSession(_Session):
        def get(self, model, identity):
            return run if model is UpdateRun and identity == "run-1" else None

    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: RunSession())
    update_executor._set_run(
        "run-1", status=UpdateStatus.COMPLETED, progress=999, message="x" * 3000
    )
    assert run.progress == 100 and len(run.message) == 2000 and run.completed_at is not None
    update_executor._set_run(
        "missing", status=UpdateStatus.FAILED, progress=-1, message="missing"
    )

    settings = _settings(tmp_path / "tls-health")
    ca = settings.data_dir / "ca.pem"
    ca.write_text("ca", encoding="utf-8")
    settings.tls_enabled = True
    settings.tls_client_ca_file = ca
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.ssl, "create_default_context", lambda **_kwargs: "context")
    monkeypatch.setattr(
        update_executor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(update_executor.urllib.error.URLError("offline")),
    )
    assert not update_executor._health_check()

    corrupt = tmp_path / "corrupt-pending"
    corrupt.mkdir()
    (corrupt / "partyops.db").write_bytes(b"not sqlite")
    assert update_executor._pending_run_id(corrupt) is None

    data = tmp_path / "supervisor-stale"
    data.mkdir()
    lock = data / ".update.lock"
    lock.write_text("{}", encoding="utf-8")
    launched: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_candidate_host_environments",
        lambda: [{"PARTYOPS_DATA_DIR": str(data)}],
    )
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: "run-2")
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: True)
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **_kwargs: launched.append(command),
    )
    assert update_executor.run_supervisor(once=True) == 0
    assert not lock.exists() and launched


def test_execute_host_update_rejects_invalid_state_and_package(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path / "host-guards")
    settings.updates_dir.mkdir()
    run = SimpleNamespace(id="run-1", package_id="package-1", target_device_id="device-1")
    package = SimpleNamespace(
        id="package-1",
        filename="missing.partyops-update",
        sha256="0" * 64,
        status=UpdateStatus.APPLYING,
    )

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, identity):
            if model is UpdateRun and identity == run.id:
                return run
            if model is UpdatePackage and identity == package.id:
                return package
            return None

    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", lambda: Session())
    assert not update_executor.execute_host_update(run.id)
    run.target_device_id = None
    package.status = UpdateStatus.FAILED
    assert not update_executor.execute_host_update(run.id)
    package.status = UpdateStatus.APPLYING
    states: list[str] = []
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, **kwargs: states.append(kwargs["message"]),
    )
    assert not update_executor.execute_host_update(run.id)
    assert "缺失或哈希不一致" in states[-1]

    broken = settings.updates_dir / package.filename
    broken.write_bytes(b"not-a-zip")
    package.sha256 = update_executor._hash(broken)
    assert not update_executor.execute_host_update(run.id)
    assert "清单损坏" in states[-1]


def test_main_dispatches_all_explicit_modes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(update_executor, "install_device_package", lambda _path: False)
    monkeypatch.setattr(sys, "argv", ["executor", "--install-package", str(tmp_path / "x.partyops-update")])
    with pytest.raises(SystemExit) as install:
        update_executor.main()
    assert install.value.code == 1

    monkeypatch.setattr(update_executor, "run_supervisor", lambda once=False: 7)
    monkeypatch.setattr(sys, "argv", ["executor", "--supervisor", "--once"])
    with pytest.raises(SystemExit) as supervisor:
        update_executor.main()
    assert supervisor.value.code == 7

    monkeypatch.setattr(update_executor, "execute_host_update", lambda _run_id: True)
    monkeypatch.setattr(sys, "argv", ["executor", "--run-id", "run-main"])
    with pytest.raises(SystemExit) as host:
        update_executor.main()
    assert host.value.code == 0
