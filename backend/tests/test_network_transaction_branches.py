"""协同网络变更事务的回滚、确认与诊断分支测试。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.problems import ProblemException
from app.routers import admin as admin_router


class ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeDb:
    def __init__(self, pending: object | None = None, devices: list[object] | None = None) -> None:
        self.pending = pending
        self.devices = devices or []
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    def get(self, _model: object, key: str) -> object | None:
        return self.pending if key == "network.pending" else None

    def scalars(self, _query: object) -> ScalarResult:
        return ScalarResult(self.devices)

    def add(self, value: object) -> None:
        self.added.append(value)

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise OSError("commit failed")

    def rollback(self) -> None:
        self.rollbacks += 1


def _settings(tmp_path: Path, **updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "secrets_dir": tmp_path / "secrets",
        "network_bind_host": "0.0.0.0",
        "network_advertise_host": "192.168.1.10",
        "port": 18765,
        "agent_port": 18766,
        "tls_enabled": False,
        "environment": "production",
    }
    values.update(updates)
    instance = SimpleNamespace(**values)
    instance.model_copy = lambda update: _settings(
        tmp_path,
        network_bind_host=update["bind_host"],
        network_advertise_host=update["advertise_host"],
        port=update["port"],
        agent_port=instance.agent_port,
        tls_enabled=instance.tls_enabled,
        environment=instance.environment,
    )
    return instance


def _request(host: str = "127.0.0.1") -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host))


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id="admin-1", role=SimpleNamespace(value="admin"))


def _problem_code(callable_: Any) -> str:
    with pytest.raises(ProblemException) as caught:
        callable_()
    return caught.value.code


def test_host_desktop_request_matrix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(admin_router, "get_settings", lambda: _settings(tmp_path, environment="test"))
    assert admin_router._request_from_host_desktop(_request("testclient")) is True
    assert admin_router._request_from_host_desktop(_request("127.0.0.1")) is True
    assert admin_router._request_from_host_desktop(_request("::ffff:127.0.0.1")) is True
    monkeypatch.setattr(admin_router, "discover_lan_addresses", lambda: ["192.168.8.3"])
    assert admin_router._request_from_host_desktop(_request("192.168.8.3")) is True
    assert admin_router._request_from_host_desktop(_request("192.168.8.4")) is False
    assert admin_router._request_from_host_desktop(_request("not-an-ip")) is False
    assert admin_router.client_ip(SimpleNamespace(client=None)) == ""


def test_network_snapshot_create_restore_and_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "network-settings.json").write_text("old", encoding="utf-8")
    pki = settings.secrets_dir / "pki"
    pki.mkdir(parents=True)
    (pki / "server.key").write_text("key", encoding="utf-8")
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    root = admin_router._create_network_snapshot("tx-snapshot")
    metadata = json.loads((root / "snapshot.json").read_text(encoding="utf-8"))
    assert set(metadata["present"]) == {"network-settings.json", "server.key"}

    (pki / "server.key").write_text("new", encoding="utf-8")
    (pki / "server.pem").write_text("remove-me", encoding="utf-8")
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(admin_router, "write_network_override", lambda value: writes.append(value))
    previous = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.10", "port": 18765}
    admin_router._restore_network_snapshot("tx-snapshot", previous)
    assert writes == [previous]
    assert (pki / "server.key").read_text(encoding="utf-8") == "key"
    assert not (pki / "server.pem").exists()
    with pytest.raises(FileNotFoundError):
        admin_router._restore_network_snapshot("missing", previous)


def test_platform_role_marker_and_posix_snapshot_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePath:
        def __init__(self, value: object) -> None:
            self.value = str(value)

        @classmethod
        def home(cls) -> "FakePath":
            return cls("HOME")

        def __truediv__(self, child: str) -> "FakePath":
            return FakePath(f"{self.value}/{child}")

        def __str__(self) -> str:
            return self.value

    with monkeypatch.context() as patcher:
        patcher.setattr(admin_router, "Path", FakePath)
        patcher.setattr(admin_router.sys, "platform", "darwin")
        assert str(admin_router._role_reconfigure_marker_path()).endswith(
            "Library/Application Support/PartyOps/Config/reconfigure-request.json"
        )
    with monkeypatch.context() as patcher:
        patcher.setattr(admin_router, "Path", FakePath)
        patcher.setattr(admin_router.sys, "platform", "linux")
        patcher.setattr(admin_router.os, "name", "posix")
        patcher.setenv("XDG_CONFIG_HOME", "/config")
        assert str(admin_router._role_reconfigure_marker_path()) == "/config/partyops/reconfigure-request.json"
    with monkeypatch.context() as patcher:
        patcher.setattr(admin_router, "Path", FakePath)
        patcher.setattr(admin_router.sys, "platform", "win32")
        patcher.setattr(admin_router.os, "name", "nt")
        patcher.setenv("LOCALAPPDATA", "C:/Local")
        assert str(admin_router._role_reconfigure_marker_path()) == "C:/Local/PartyOps/reconfigure-request.json"

    settings = _settings(tmp_path / "posix")
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "network-settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    with monkeypatch.context() as patcher:
        patcher.setattr(admin_router.os, "name", "posix")
        root = admin_router._create_network_snapshot("tx-posix")
    assert root.is_dir()
    assert (root / "snapshot.json").is_file()

    empty_settings = _settings(tmp_path / "empty")
    monkeypatch.setattr(admin_router, "get_settings", lambda: empty_settings)
    admin_router._create_network_snapshot("tx-empty")
    overrides: list[dict[str, object]] = []
    monkeypatch.setattr(admin_router, "write_network_override", lambda value: overrides.append(value))
    previous = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.5", "port": 18765}
    admin_router._restore_network_snapshot("tx-empty", previous)
    assert overrides == [previous]


def test_audit_actor_filter_branch() -> None:
    db = FakeDb()
    db.scalars = lambda _query: ScalarResult([])  # type: ignore[method-assign]
    assert admin_router.list_audit(None, "actor-1", 10, _admin(), db) == []


def test_probe_network_health_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = {"advertise_host": "192.168.1.20", "port": 18765}
    settings = _settings(tmp_path)
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)

    class Response:
        def __init__(self, status: int, payload: bytes) -> None:
            self.status = status
            self.payload = payload

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return self.payload

    monkeypatch.setattr(admin_router.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(200, b'{"version":"1.4.5-rc.2"}'))
    assert admin_router._probe_network_health(value)["version"] == "1.4.5-rc.2"
    monkeypatch.setattr(admin_router.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(503, b"[]"))
    with pytest.raises(OSError, match="异常"):
        admin_router._probe_network_health(value)

    settings.tls_enabled = True
    with pytest.raises(OSError, match="CA"):
        admin_router._probe_network_health(value)
    ca = settings.secrets_dir / "pki" / "ca.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("test-ca", encoding="utf-8")
    monkeypatch.setattr(admin_router.ssl, "create_default_context", lambda **_kwargs: object())
    monkeypatch.setattr(admin_router.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(200, b"{}"))
    assert admin_router._probe_network_health(value)["status"] == 200


def test_patch_network_configuration_branch_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    old = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.10", "port": 18765}
    new = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.11", "port": 18766}
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin_router, "validate_network_payload", lambda _payload: new)
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: False)
    assert _problem_code(lambda: admin_router.patch_network_configuration({}, _request(), _admin(), FakeDb())) == "NETWORK_UPDATE_LOCAL_REQUIRED"

    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: True)
    monkeypatch.setattr(admin_router, "validate_network_payload", lambda _payload: old)
    unchanged = admin_router.patch_network_configuration({}, _request(), _admin(), FakeDb())
    assert unchanged["changed"] is False

    snapshots: list[str] = []
    restores: list[str] = []
    monkeypatch.setattr(admin_router, "validate_network_payload", lambda _payload: new)
    monkeypatch.setattr(admin_router, "_create_network_snapshot", lambda tx: snapshots.append(tx) or tmp_path)
    monkeypatch.setattr(admin_router, "_restore_network_snapshot", lambda tx, _old: restores.append(tx))
    monkeypatch.setattr(admin_router, "write_network_override", lambda _value: (_ for _ in ()).throw(OSError()))
    assert _problem_code(lambda: admin_router.patch_network_configuration({}, _request(), _admin(), FakeDb())) == "NETWORK_UPDATE_ROLLED_BACK"
    assert restores[-1] == snapshots[-1]

    monkeypatch.setattr(admin_router, "write_network_override", lambda _value: None)
    pending = SimpleNamespace(value={"old": True})
    db = FakeDb(pending=pending, devices=[SimpleNamespace(id="device-1")])
    changed = admin_router.patch_network_configuration({"migration_grace_hours": 0}, _request(), _admin(), db)
    assert changed["changed"] is True and changed["device_notifications"] == 1
    assert pending.value["state"] == "restart_required"
    assert db.commits == 1

    db = FakeDb()
    db.fail_commit = True
    assert _problem_code(lambda: admin_router.patch_network_configuration({}, _request(), _admin(), db)) == "NETWORK_TRANSACTION_ROLLED_BACK"
    assert db.rollbacks == 1


def test_get_and_confirm_network_transaction_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transaction_id = "tx-confirm"
    pending = SimpleNamespace(value={"transaction_id": transaction_id, "state": "restart_required"})
    db = FakeDb(pending=pending)
    assert admin_router.get_network_transaction(transaction_id, _admin(), db)["state"] == "restart_required"
    assert _problem_code(lambda: admin_router.get_network_transaction("missing", _admin(), db)) == "NETWORK_TRANSACTION_NOT_FOUND"

    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: False)
    assert _problem_code(lambda: admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_CONFIRM_LOCAL_REQUIRED"
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: True)
    assert _problem_code(lambda: admin_router.confirm_network_transaction("missing", _request(), _admin(), db)) == "NETWORK_TRANSACTION_NOT_FOUND"
    pending.value = {"transaction_id": transaction_id, "state": "active", "health": {"status": 200}}
    assert admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)["state"] == "active"
    pending.value = {"transaction_id": transaction_id, "state": "restart_required", "requested": None}
    assert _problem_code(lambda: admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_TRANSACTION_INVALID"

    requested = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.20", "port": 18766}
    pending.value = {"transaction_id": transaction_id, "state": "restart_required", "requested": requested}
    monkeypatch.setattr(admin_router, "get_settings", lambda: _settings(tmp_path))
    assert _problem_code(lambda: admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_RESTART_REQUIRED"
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: _settings(tmp_path, network_advertise_host="192.168.1.20", port=18766),
    )
    monkeypatch.setattr(admin_router, "_probe_network_health", lambda _value: (_ for _ in ()).throw(OSError()))
    assert _problem_code(lambda: admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_HEALTH_CHECK_FAILED"
    monkeypatch.setattr(admin_router, "_probe_network_health", lambda _value: {"status": 200})
    monkeypatch.setattr(admin_router, "write_audit", lambda *_args, **_kwargs: None)
    result = admin_router.confirm_network_transaction(transaction_id, _request(), _admin(), db)
    assert result["state"] == "active" and result["health"]["status"] == 200


def test_rollback_network_transaction_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transaction_id = "tx-rollback"
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: False)
    db = FakeDb(pending=SimpleNamespace(value={"transaction_id": transaction_id}))
    assert _problem_code(lambda: admin_router.rollback_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_ROLLBACK_LOCAL_REQUIRED"
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: True)
    assert _problem_code(lambda: admin_router.rollback_network_transaction("missing", _request(), _admin(), db)) == "NETWORK_TRANSACTION_NOT_FOUND"

    db.pending.value = {"transaction_id": transaction_id, "state": "rolled_back"}
    assert admin_router.rollback_network_transaction(transaction_id, _request(), _admin(), db)["changed"] is False
    db.pending.value = {"transaction_id": transaction_id, "state": "active", "previous": None}
    assert _problem_code(lambda: admin_router.rollback_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_ROLLBACK_SNAPSHOT_INVALID"

    previous = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.10", "port": 18765}
    db.pending.value = {"transaction_id": transaction_id, "state": "active", "previous": previous}
    monkeypatch.setattr(admin_router, "_restore_network_snapshot", lambda *_args: (_ for _ in ()).throw(OSError()))
    assert _problem_code(lambda: admin_router.rollback_network_transaction(transaction_id, _request(), _admin(), db)) == "NETWORK_ROLLBACK_FAILED"

    monkeypatch.setattr(admin_router, "_restore_network_snapshot", lambda *_args: None)
    monkeypatch.setattr(admin_router, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(admin_router, "write_audit", lambda *_args, **_kwargs: None)
    db = FakeDb(
        pending=SimpleNamespace(
            value={"transaction_id": transaction_id, "state": "active", "previous": previous, "migration_grace_hours": 0}
        ),
        devices=[SimpleNamespace(id="device-1"), SimpleNamespace(id="device-2")],
    )
    result = admin_router.rollback_network_transaction(transaction_id, _request(), _admin(), db)
    assert result["changed"] is True and result["device_notifications"] == 2
    assert db.pending.value["state"] == "rolled_back"


def test_bounded_upload_and_recent_logs_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Upload:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = iter(chunks)

        async def read(self, _size: int) -> bytes:
            return next(self.chunks, b"")

    assert asyncio.run(admin_router._write_bounded_upload(Upload([b"ab", b"cd"]), tmp_path / "ok.bin", 4)) == 4
    with pytest.raises(ProblemException) as too_large:
        asyncio.run(admin_router._write_bounded_upload(Upload([b"abc"]), tmp_path / "large.bin", 2))
    assert too_large.value.code == "BACKUP_UPLOAD_TOO_LARGE"

    logs = tmp_path / "logs"
    settings = SimpleNamespace(logs_dir=logs)
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    assert admin_router.recent_logs(20, _admin()) == "暂无运行日志。"
    logs.mkdir()
    (logs / "partyops.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert admin_router.recent_logs(2, _admin()) == "two\nthree"


def test_download_backup_authorization_and_missing_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import fleet

    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(admin_router, "get_settings", lambda: SimpleNamespace(backups_dir=backups))
    authenticated: list[str] = []
    monkeypatch.setattr(fleet, "authenticated_device", lambda token, _db: authenticated.append(token))
    pairing = SimpleNamespace(last_pull_at=None)
    monkeypatch.setattr(admin_router, "authenticate_backup_pairing", lambda _db, _token: pairing)
    record = SimpleNamespace(filename="backup.partyops-backup", sha256="abc", status="completed")

    class BackupDb:
        def __init__(self, value: object | None) -> None:
            self.value = value
            self.commits = 0

        def get(self, _model: object, _key: str) -> object | None:
            return self.value

        def commit(self) -> None:
            self.commits += 1

    db = BackupDb(record)
    assert _problem_code(lambda: admin_router.download_backup("id", None, None, None, db)) == "BACKUP_DOWNLOAD_FORBIDDEN"
    assert _problem_code(lambda: admin_router.download_backup("id", None, None, _admin(), BackupDb(None))) == "BACKUP_NOT_FOUND"
    assert _problem_code(lambda: admin_router.download_backup("id", None, None, _admin(), db)) == "BACKUP_FILE_MISSING"
    (backups / record.filename).write_bytes(b"backup")
    response = admin_router.download_backup("id", None, "device-token", None, db)
    assert response.headers["x-partyops-sha256"] == "abc" and authenticated == ["device-token"]
    response = admin_router.download_backup("id", "pairing-token", None, None, db)
    assert response.headers["x-partyops-sha256"] == "abc" and db.commits == 1
    assert pairing.last_pull_at is not None


def test_latest_backup_and_paired_summary_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import fleet

    backups = tmp_path / "backups"
    backups.mkdir()
    monkeypatch.setattr(admin_router, "get_settings", lambda: SimpleNamespace(backups_dir=backups))
    authenticated: list[str] = []
    monkeypatch.setattr(fleet, "authenticated_device", lambda token, _db: authenticated.append(token))
    pairing = SimpleNamespace(last_pull_at=None)
    monkeypatch.setattr(admin_router, "authenticate_backup_pairing", lambda _db, _token: pairing)
    record = SimpleNamespace(id="backup-1", filename="backup.zip", sha256="sha", status="completed")
    (backups / record.filename).write_bytes(b"backup")

    class LatestDb:
        def __init__(self, record_value: object | None) -> None:
            self.record_value = record_value
            self.commits = 0

        def scalar(self, _query: object) -> object | None:
            return self.record_value

        def commit(self) -> None:
            self.commits += 1

    assert _problem_code(lambda: admin_router.latest_backup("token", None, None, LatestDb(None))) == "BACKUP_NOT_FOUND"
    response = admin_router.latest_backup(None, "device", None, LatestDb(record))
    assert response.headers["x-partyops-backup-id"] == "backup-1" and authenticated == ["device"]
    paired_db = LatestDb(record)
    unchanged = admin_router.latest_backup("token", None, '"sha"', paired_db)
    assert unchanged.status_code == 304 and paired_db.commits == 1 and pairing.last_pull_at is not None

    users = [SimpleNamespace(id="user-1"), SimpleNamespace(id="user-2")]

    class SummaryDb:
        def __init__(self, scalar_values: list[object]) -> None:
            self.scalar_values = iter(scalar_values)

        def scalars(self, _query: object) -> ScalarResult:
            return ScalarResult(users)

        def get(self, _model: object, key: str) -> object:
            return SimpleNamespace(enabled=key == "user-1")

        def scalar(self, _query: object) -> object:
            return next(self.scalar_values)

    monkeypatch.setattr(admin_router, "desktop_notifications_allowed", lambda preference: preference.enabled)
    summary = admin_router.paired_notification_summary(None, "device-summary", SummaryDb([3, None]))
    assert summary == {"unread_count": 3, "revision": ""}
    monkeypatch.setattr(admin_router, "desktop_notifications_allowed", lambda _preference: False)
    empty = admin_router.paired_notification_summary("pairing", None, SummaryDb([]))
    assert empty == {"unread_count": 0, "revision": ""}


def test_tls_network_patch_rotates_certificate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import pki

    settings = _settings(tmp_path, tls_enabled=True)
    new = {"bind_host": "0.0.0.0", "advertise_host": "192.168.1.22", "port": 18765}
    rotated: list[str] = []
    monkeypatch.setattr(admin_router, "get_settings", lambda: settings)
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: True)
    monkeypatch.setattr(admin_router, "validate_network_payload", lambda _payload: new)
    monkeypatch.setattr(admin_router, "_create_network_snapshot", lambda _tx: tmp_path)
    monkeypatch.setattr(admin_router, "write_network_override", lambda _value: None)
    monkeypatch.setattr(admin_router, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pki, "ensure_tls_material", lambda candidate: rotated.append(candidate.network_advertise_host))
    result = admin_router.patch_network_configuration({}, _request(), _admin(), FakeDb())
    assert result["certificate_rotated"] is True and rotated == ["192.168.1.22"]
