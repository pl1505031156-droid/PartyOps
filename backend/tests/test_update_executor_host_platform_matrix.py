"""主机应用内升级的跨平台分派、DEB 原位安装与回滚对抗矩阵。"""

from __future__ import annotations

import subprocess
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import update_executor
from app.enums import UpdateStatus
from app.models import UpdatePackage, UpdateRun


def test_database_snapshot_restore_disposes_wal_pool_before_atomic_switch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "partyops.db"
    snapshot = tmp_path / "snapshot.db"
    connection = sqlite3.connect(destination)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state VALUES ('old')")
        connection.commit()
    finally:
        connection.close()
    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state VALUES ('new')")
        connection.commit()
    finally:
        connection.close()

    class Runtime:
        def __init__(self) -> None:
            self.connection = sqlite3.connect(destination)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.disposed = False
            self.rebuilt = False

        @contextmanager
        def exclusive_maintenance(self, **_kwargs):
            yield

        def dispose(self) -> None:
            self.connection.close()
            self.disposed = True

        def rebuild(self) -> None:
            self.rebuilt = True

    runtime = Runtime()
    monkeypatch.setattr(update_executor, "db_runtime", runtime)
    update_executor._restore_database_snapshot(snapshot, destination)

    assert runtime.disposed and runtime.rebuilt
    connection = sqlite3.connect(destination)
    try:
        assert connection.execute("SELECT value FROM state").fetchone() == ("new",)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        connection.close()


def test_database_snapshot_restore_rejects_corruption_and_recovers_failed_switch(
    monkeypatch, tmp_path: Path
) -> None:
    """坏快照不得替换主库；第二次原子换名失败时必须放回原主库。"""

    destination = tmp_path / "partyops.db"
    snapshot = tmp_path / "snapshot.db"
    for path, value in ((destination, "old"), (snapshot, "new")):
        connection = sqlite3.connect(path)
        try:
            connection.execute("CREATE TABLE state (value TEXT)")
            connection.execute("INSERT INTO state VALUES (?)", (value,))
            connection.commit()
        finally:
            connection.close()

    class Runtime:
        def __init__(self) -> None:
            self.disposed = False
            self.rebuilt = False

        @contextmanager
        def exclusive_maintenance(self, **_kwargs):
            yield

        def dispose(self) -> None:
            self.disposed = True

        def rebuild(self) -> None:
            self.rebuilt = True

    runtime = Runtime()
    monkeypatch.setattr(update_executor, "db_runtime", runtime)
    real_replace = update_executor._atomic_replace
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated rename denial")
        real_replace(source, target)

    monkeypatch.setattr(update_executor, "_atomic_replace", fail_second_replace)
    with pytest.raises(OSError, match="rename denial"):
        update_executor._restore_database_snapshot(snapshot, destination)
    assert runtime.disposed and runtime.rebuilt
    restored = sqlite3.connect(destination)
    try:
        assert restored.execute("SELECT value FROM state").fetchone() == ("old",)
    finally:
        restored.close()
    assert not destination.with_name(f".{destination.name}.rollback-incoming").exists()
    assert not destination.with_name(f".{destination.name}.before-rollback").exists()

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        update_executor._restore_database_snapshot(corrupt, destination)


def test_database_snapshot_restore_can_create_missing_destination(
    monkeypatch, tmp_path: Path
) -> None:
    destination = tmp_path / "new" / "partyops.db"
    destination.parent.mkdir()
    snapshot = tmp_path / "snapshot.db"
    connection = sqlite3.connect(snapshot)
    try:
        connection.execute("CREATE TABLE state (value TEXT)")
        connection.execute("INSERT INTO state VALUES ('new')")
        connection.commit()
    finally:
        connection.close()

    class Runtime:
        @contextmanager
        def exclusive_maintenance(self, **_kwargs):
            yield

        def dispose(self) -> None:
            return None

        def rebuild(self) -> None:
            return None

    monkeypatch.setattr(update_executor, "db_runtime", Runtime())
    update_executor._restore_database_snapshot(snapshot, destination)
    restored = sqlite3.connect(destination)
    try:
        assert restored.execute("SELECT value FROM state").fetchone() == ("new",)
    finally:
        restored.close()


class _Session:
    def __init__(self, run, package) -> None:
        self.run = run
        self.package = package
        self.commits = 0

    def get(self, model, identity):
        if model is UpdateRun and self.run is not None and identity == self.run.id:
            return self.run
        if model is UpdatePackage and self.package is not None and identity == self.package.id:
            return self.package
        return None

    def commit(self) -> None:
        self.commits += 1


def _fixture(monkeypatch, tmp_path: Path, *, platform_name: str = "linux-deb"):
    data_dir = tmp_path / "data"
    updates_dir = data_dir / "updates"
    updates_dir.mkdir(parents=True)
    settings = SimpleNamespace(
        data_dir=data_dir,
        updates_dir=updates_dir,
        database_path=data_dir / "partyops.db",
        attachments_dir=data_dir / "attachments",
        archives_dir=data_dir / "archives",
        app_version="1.4.3-rc.2",
    )
    package_path = updates_dir / "release.partyops-update"
    package_path.write_bytes(b"signed-package")
    package = SimpleNamespace(
        id="package-1",
        filename=package_path.name,
        sha256=update_executor._hash(package_path),
        status=UpdateStatus.APPLYING,
    )
    run = SimpleNamespace(
        id="run-host-platform",
        package_id=package.id,
        target_device_id=None,
        status=UpdateStatus.APPLYING,
        progress=5,
        message="",
        completed_at=None,
    )
    session = _Session(run, package)

    @contextmanager
    def factory():
        yield session

    states: list[tuple[UpdateStatus, str]] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", factory)
    monkeypatch.setattr(
        update_executor,
        "_read_update_manifest",
        lambda _path: {"format_version": 4, "version": "1.4.3-rc.3"},
    )
    monkeypatch.setattr(update_executor, "_manifest_platform_name", lambda _manifest: platform_name)
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, *, status, progress, message: states.append((status, message)),
    )
    return settings, package_path, package, run, states


@pytest.mark.parametrize(
    ("platform_name", "windows_artifact", "expected", "adapter"),
    [
        ("windows", True, True, "windows"),
        ("windows7", True, False, "windows"),
        ("linux-rpm", True, True, "rpm"),
        ("unsupported", True, False, "none"),
    ],
)
def test_host_update_dispatches_exact_platform_adapter(
    monkeypatch,
    tmp_path: Path,
    platform_name: str,
    windows_artifact: bool,
    expected: bool,
    adapter: str,
) -> None:
    _settings, _path, _package, run, states = _fixture(
        monkeypatch,
        tmp_path,
        platform_name=platform_name,
    )
    called: list[str] = []
    monkeypatch.setattr(update_executor, "_manifest_has_windows_artifact", lambda _manifest: windows_artifact)
    monkeypatch.setattr(
        update_executor,
        "_execute_windows_host_update",
        lambda *_args: called.append("windows") or expected,
    )
    monkeypatch.setattr(
        update_executor,
        "_execute_linux_rpm_host_update",
        lambda *_args: called.append("rpm") or expected,
    )
    assert update_executor.execute_host_update(run.id) is expected
    assert called == ([] if adapter == "none" else [adapter])
    if adapter == "none":
        assert states[-1][0] == UpdateStatus.FAILED


def test_host_update_rejects_windows_package_without_current_architecture(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _settings, _path, _package, run, states = _fixture(monkeypatch, tmp_path, platform_name="windows")
    monkeypatch.setattr(update_executor, "_manifest_has_windows_artifact", lambda _manifest: False)
    assert not update_executor.execute_host_update(run.id)
    assert states[-1][0] == UpdateStatus.FAILED


def _configure_deb_transaction(monkeypatch, settings, *, stop_code: int = 0):
    monkeypatch.setattr(update_executor, "_ensure_dpkg_ready", lambda: True)
    monkeypatch.setattr(update_executor, "_ensure_update_snapshot_space", lambda *_args: None)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")

    def snapshot(destination: Path) -> None:
        destination.write_bytes(b"rollback-deb")

    def select(_package, _manifest, _architecture, target: Path, _platform="uos"):
        target.write_bytes(b"new-deb")
        return target

    monkeypatch.setattr(update_executor, "_create_installed_package_snapshot", snapshot)
    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            stop_code if command[:3] == ["systemctl", "stop", "partyops"] else 0,
            "",
            "",
        ),
    )
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_args: True)
    monkeypatch.setattr(update_executor, "_queue_device_updates", lambda *_args: 0)


def test_deb_update_lock_dpkg_and_service_fail_before_mutation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings, _path, _package, run, states = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    assert not update_executor.execute_host_update(run.id)

    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    monkeypatch.setattr(update_executor, "_ensure_dpkg_ready", lambda: False)
    assert not update_executor.execute_host_update(run.id)
    assert states[-1][0] == UpdateStatus.FAILED

    _configure_deb_transaction(monkeypatch, settings, stop_code=1)
    assert not update_executor.execute_host_update(run.id)
    assert "原版本保持不变" in states[-1][1]


def test_deb_update_success_without_optional_data_trees(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings, _path, package, run, states = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    _configure_deb_transaction(monkeypatch, settings)
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    assert update_executor.execute_host_update(run.id)
    assert run.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED
    assert states[-1][0] == UpdateStatus.APPLYING


def test_deb_update_failed_install_restores_package_and_reports_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings, _path, _package, run, states = _fixture(monkeypatch, tmp_path)
    settings.database_path.write_bytes(b"database")
    settings.attachments_dir.mkdir()
    settings.archives_dir.mkdir()
    (settings.attachments_dir / "a.txt").write_text("a", encoding="utf-8")
    (settings.archives_dir / "b.txt").write_text("b", encoding="utf-8")
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    _configure_deb_transaction(monkeypatch, settings)
    monkeypatch.setattr(
        update_executor,
        "_online_backup_database",
        lambda source, destination: destination.write_bytes(source.read_bytes()),
    )
    monkeypatch.setattr(
        update_executor,
        "_restore_database_snapshot",
        lambda source, destination: destination.write_bytes(source.read_bytes()),
    )
    results = iter([1, 0, 0])
    monkeypatch.setattr(
        update_executor,
        "_run_linux_package_manager",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, next(results), "", ""),
    )
    assert not update_executor.execute_host_update(run.id)
    assert states[-1][0] == UpdateStatus.ROLLED_BACK
