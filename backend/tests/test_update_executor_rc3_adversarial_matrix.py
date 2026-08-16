"""rc.3 RPM 更新成功/回滚矩阵，覆盖真实发布门禁的双向分支。"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import update_executor
from app.enums import UpdateStatus
from app.models import UpdatePackage, UpdateRun


def _settings(tmp_path: Path) -> SimpleNamespace:
    data_dir = tmp_path / "data"
    attachments = data_dir / "attachments"
    archives = data_dir / "archives"
    transfers = data_dir / "transfers"
    for directory in (attachments, archives, transfers):
        directory.mkdir(parents=True)
    (attachments / "material.txt").write_text("material", encoding="utf-8")
    (archives / "archive.txt").write_text("archive", encoding="utf-8")
    database = data_dir / "partyops.db"
    database.write_bytes(b"sqlite-backup-source")
    return SimpleNamespace(
        data_dir=data_dir,
        database_path=database,
        attachments_dir=attachments,
        archives_dir=archives,
        transfers_dir=transfers,
    )


def test_rpm_host_update_success_covers_snapshots_health_and_queue(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    package_path = tmp_path / "release.partyops-update"
    package_path.write_bytes(b"signed-update")
    run = SimpleNamespace(
        id="run-rpm-success",
        package_id="package-1",
        status=UpdateStatus.APPLYING,
        progress=0,
        message="",
        completed_at=None,
    )
    package = SimpleNamespace(id="package-1", status=UpdateStatus.APPLYING)

    class Session:
        def get(self, model, identity):
            if model is UpdateRun and identity == run.id:
                return run
            if model is UpdatePackage and identity == package.id:
                return package
            return None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    package_cache = tmp_path / "cache" / "current.rpm"
    package_cache.parent.mkdir()
    package_cache.write_bytes(b"old-rpm")
    update_executor._rollback_digest_path(package_cache).write_text(
        update_executor._hash(package_cache),
        encoding="ascii",
    )

    def copy2(source, destination, *args, **kwargs):
        target = Path(destination)
        if str(target) == "/var/cache/partyops/current.rpm":
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"snapshot")
        return target

    def select(_package, _manifest, _architecture, target, _platform):
        target.write_bytes(b"new-rpm")
        return target

    states: list[tuple[UpdateStatus, int]] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", package_cache)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    monkeypatch.setattr(update_executor.shutil, "copy2", copy2)
    monkeypatch.setattr(update_executor, "_online_backup_database", copy2)
    monkeypatch.setattr(update_executor, "_restore_database_snapshot", copy2)
    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_install_rpm", lambda _path, **_kwargs: True)
    monkeypatch.setattr(update_executor, "_health_check", lambda *_args: True)
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(update_executor, "_queue_device_updates", lambda *_a: 2)
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", factory)
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, *, status, progress, message: states.append((status, progress)),
    )

    assert update_executor._execute_linux_rpm_host_update(run.id, package_path, {})
    assert run.status == UpdateStatus.COMPLETED
    assert package.status == UpdateStatus.COMPLETED
    assert not (settings.data_dir / "upgrade-backups" / run.id).exists()
    assert states[-1] == (UpdateStatus.APPLYING, 80)


@pytest.mark.parametrize(
    ("rollback_health", "expected_status"),
    [
        (True, UpdateStatus.ROLLED_BACK),
        (False, UpdateStatus.FAILED),
    ],
)
def test_rpm_host_update_failure_restores_program_database_and_trees(
    monkeypatch,
    tmp_path: Path,
    rollback_health: bool,
    expected_status: UpdateStatus,
) -> None:
    settings = _settings(tmp_path)
    package_path = tmp_path / "release.partyops-update"
    package_path.write_bytes(b"signed-update")
    run_id = "run-rpm-failure"
    package_cache = tmp_path / "cache" / "current.rpm"
    package_cache.parent.mkdir()
    package_cache.write_bytes(b"old-rpm")
    update_executor._rollback_digest_path(package_cache).write_text(
        update_executor._hash(package_cache),
        encoding="ascii",
    )

    def copy2(source, destination, *args, **kwargs):
        target = Path(destination)
        if str(target) == "/var/cache/partyops/current.rpm":
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source).read_bytes() if Path(source).exists() else b"rollback")
        return target

    def select(_package, _manifest, _architecture, target, _platform):
        target.write_bytes(b"bad-rpm")
        return target

    installer_results = iter([False, True])
    states: list[UpdateStatus] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", package_cache)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    monkeypatch.setattr(update_executor.shutil, "copy2", copy2)
    monkeypatch.setattr(update_executor, "_online_backup_database", copy2)
    monkeypatch.setattr(update_executor, "_restore_database_snapshot", copy2)
    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(
        update_executor,
        "_install_rpm",
        lambda _path, **_kwargs: next(installer_results),
    )
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(
        update_executor,
        "_wait_for_health",
        lambda *_args: rollback_health,
    )
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, *, status, progress, message: states.append(status),
    )

    assert not update_executor._execute_linux_rpm_host_update(run_id, package_path, {})
    assert states[-1] == expected_status
    assert settings.database_path.read_bytes() == b"sqlite-backup-source"
    assert (settings.attachments_dir / "material.txt").is_file()
    assert (settings.archives_dir / "archive.txt").is_file()


def test_rpm_host_update_refuses_missing_cache_and_busy_lock(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    states: list[UpdateStatus] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor,
        "RPM_PACKAGE_CACHE",
        tmp_path / "missing-cache" / "current.rpm",
    )
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: False)
    assert not update_executor._execute_linux_rpm_host_update("busy", tmp_path / "x", {})

    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    commands: list[list[str]] = []
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, *, status, progress, message: states.append(status),
    )
    assert not update_executor._execute_linux_rpm_host_update("missing", tmp_path / "x", {})
    assert states[-1] == UpdateStatus.FAILED
    assert commands == []


def test_rpm_host_update_without_optional_data_or_database(
    monkeypatch, tmp_path: Path
) -> None:
    """空白新主机也能升级；没有可选目录时不得构造虚假快照。"""

    settings = _settings(tmp_path)
    settings.database_path.unlink()
    for directory in (settings.attachments_dir, settings.archives_dir):
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
    package_path = tmp_path / "release.partyops-update"
    package_path.write_bytes(b"signed-update")
    package_cache = tmp_path / "cache" / "current.rpm"
    package_cache.parent.mkdir()
    package_cache.write_bytes(b"old-rpm")
    update_executor._rollback_digest_path(package_cache).write_text(
        update_executor._hash(package_cache), encoding="ascii"
    )

    class Session:
        def get(self, _model, _identity):
            return None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    def select(_package, _manifest, _architecture, target, _platform):
        target.write_bytes(b"new-rpm")
        return target

    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", package_cache)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(update_executor, "_install_rpm", lambda *_a, **_k: True)
    monkeypatch.setattr(update_executor, "_wait_for_health", lambda *_a: True)
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )
    monkeypatch.setattr(update_executor.db_runtime, "session_factory", factory)
    monkeypatch.setattr(update_executor, "_set_run", lambda *_a, **_k: None)
    assert update_executor._execute_linux_rpm_host_update(
        "run-rpm-empty", package_path, {"version": "1.4.3-rc.3"}
    )


def test_rpm_service_stop_failure_never_mutates_program(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    package_path = tmp_path / "release.partyops-update"
    package_path.write_bytes(b"signed-update")
    package_cache = tmp_path / "cache" / "current.rpm"
    package_cache.parent.mkdir()
    package_cache.write_bytes(b"old-rpm")
    update_executor._rollback_digest_path(package_cache).write_text(
        update_executor._hash(package_cache), encoding="ascii"
    )

    def select(_package, _manifest, _architecture, target, _platform):
        target.write_bytes(b"new-rpm")
        return target

    states: list[tuple[UpdateStatus, str]] = []
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(update_executor, "RPM_PACKAGE_CACHE", package_cache)
    monkeypatch.setattr(update_executor, "_acquire_update_lock", lambda _path: True)
    monkeypatch.setattr(update_executor, "_select_artifact", select)
    monkeypatch.setattr(update_executor, "_architecture", lambda: "amd64")
    monkeypatch.setattr(
        update_executor,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            5 if command[:3] == ["systemctl", "stop", "partyops"] else 0,
            "",
            "",
        ),
    )
    monkeypatch.setattr(
        update_executor,
        "_set_run",
        lambda _id, *, status, progress, message: states.append((status, message)),
    )
    assert not update_executor._execute_linux_rpm_host_update(
        "run-rpm-stop-failed", package_path, {}
    )
    assert states[-1][0] == UpdateStatus.FAILED
    assert "修改程序前停止" in states[-1][1]
