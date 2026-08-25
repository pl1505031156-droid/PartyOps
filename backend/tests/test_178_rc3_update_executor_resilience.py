"""更新锁、监督器和特权环境剩余安全分支回归。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import update_executor


def test_rollback_cache_and_personal_metadata_io_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "rollback.deb"
    artifact.write_bytes(b"rollback")
    update_executor._rollback_digest_path(artifact).write_text("a" * 64, encoding="ascii")
    monkeypatch.setattr(
        update_executor,
        "_hash",
        lambda _path: (_ for _ in ()).throw(OSError("read denied")),
    )
    assert not update_executor._verify_cached_rollback_artifact(artifact)

    metadata = tmp_path / "metadata.json"
    metadata.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(
        update_executor,
        "_personal_native_rollback_paths",
        lambda _run_id: (artifact, metadata),
    )
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(name="posix", getenv=os.getenv),
    )
    monkeypatch.setattr(update_executor, "sys", SimpleNamespace(platform="linux"))
    assert not update_executor._rollback_linux_personal_package_locked("a" * 32)


def test_lock_stat_unlink_and_failed_write_are_conservative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock = tmp_path / "update.lock"
    original_read_text = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **kwargs: (_ for _ in ()).throw(OSError("acl denied"))
        if self == lock
        else original_read_text(self, **kwargs),
    )
    assert not update_executor._update_lock_is_stale(lock)

    monkeypatch.undo()
    lock.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: True)
    original_unlink = Path.unlink
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("unlink denied"))
        if self == lock
        else original_unlink(self, *args, **kwargs),
    )
    assert not update_executor._acquire_update_lock(lock)

    monkeypatch.undo()
    original_write = os.write
    monkeypatch.setattr(
        update_executor.os,
        "write",
        lambda fd, payload: (_ for _ in ()).throw(OSError("disk full")),
    )
    original_unlink = Path.unlink
    monkeypatch.setattr(
        Path,
        "unlink",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError("cleanup denied"))
        if self == lock
        else original_unlink(self, *args, **kwargs),
    )
    assert not update_executor._acquire_update_lock(lock)
    monkeypatch.setattr(update_executor.os, "write", original_write)


def test_trusted_key_rejects_unsafe_owner_and_continues_after_read_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    key = tmp_path / "update-public-key.txt"
    key.write_text("public-key", encoding="utf-8")
    monkeypatch.setattr(
        update_executor,
        "get_settings",
        lambda: SimpleNamespace(environment="production", update_public_key=""),
    )
    executable = tmp_path / "PartyOps.app" / "Contents" / "MacOS" / "PartyOps"
    monkeypatch.setattr(
        update_executor,
        "sys",
        SimpleNamespace(platform="darwin", executable=str(executable)),
    )
    expected = executable.parent.parent / "Resources" / "update-public-key.txt"
    expected.parent.mkdir(parents=True)
    expected.write_text("public-key", encoding="utf-8")

    metadata = expected.lstat()
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=1000,
            st_size=metadata.st_size,
        )
        if self == expected
        else metadata,
    )
    monkeypatch.setattr(
        update_executor,
        "os",
        SimpleNamespace(name="posix", getenv=os.getenv),
    )
    assert update_executor._trusted_public_key() == ""

    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=0,
            st_size=metadata.st_size,
        ),
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **_kwargs: (_ for _ in ()).throw(OSError("read denied")),
    )
    assert update_executor._trusted_public_key() == ""


def test_update_daemon_backoff_and_supervisor_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    class EmptyDb:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _query):
            return None

    def session_factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("select", {}, RuntimeError("locked"))
        return EmptyDb()

    sleeps: list[int] = []

    def sleep(seconds: int) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise RuntimeError("stop daemon")

    monkeypatch.setattr(update_executor.db_runtime, "session_factory", session_factory)
    monkeypatch.setattr(update_executor.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="stop daemon"):
        update_executor.run_daemon(once=False)
    assert sleeps == [3, 3]

    data_dir = tmp_path / "host-data"
    data_dir.mkdir()
    lock = data_dir / ".update.lock"
    lock.write_text(json.dumps({"pid": 0}), encoding="utf-8")
    monkeypatch.setattr(
        update_executor,
        "_candidate_host_environments",
        lambda: [{"PARTYOPS_DATA_DIR": str(data_dir), "PARTYOPS_MODE": "host"}],
    )
    monkeypatch.setattr(update_executor, "_pending_run_id", lambda _path: "a" * 32)
    monkeypatch.setattr(update_executor, "_update_lock_path", lambda _path: lock)
    monkeypatch.setattr(update_executor, "_update_lock_is_stale", lambda _path: True)
    processes: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        update_executor.subprocess,
        "Popen",
        lambda command, **kwargs: processes.append((command, kwargs)),
    )
    assert update_executor.run_supervisor(once=True) == 0
    assert processes and not lock.exists()
