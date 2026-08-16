"""设备上下文令牌、版本门禁和升级幂等分支回归。"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import device_versions
from app.enums import UpdateStatus
from app.models import Device, SystemSetting, UpdatePackage
from app.problems import ProblemException


class FakeDb:
    def __init__(self) -> None:
        self.setting = None
        self.device = None
        self.scalar_values: list[object] = []
        self.added: list[object] = []
        self.flushes = 0

    def get(self, model, _identity):
        if model is SystemSetting:
            return self.setting
        if model is Device:
            return self.device
        if model is UpdatePackage:
            return None
        return None

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flushes += 1
        for value in self.added:
            if hasattr(value, "progress") and value.progress is None:
                value.progress = 0

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None


def _device(**overrides):
    values = {
        "id": "device-1",
        "name": "协同机",
        "active": True,
        "status": "online",
        "app_version": "1.4.2",
        "agent_version": "1.4.2",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_secret_token_and_release_note_matrix(monkeypatch) -> None:
    db = FakeDb()
    db.setting = SimpleNamespace(value="x" * 64)
    assert device_versions.ensure_device_context_secret(db) == "x" * 64
    assert db.flushes == 0

    db.setting = SimpleNamespace(value="short")
    replaced = device_versions.ensure_device_context_secret(db)
    assert len(replaced) == 64 and db.setting.value == replaced and db.flushes == 1
    db.setting = None
    created = device_versions.ensure_device_context_secret(db)
    assert len(created) == 64 and isinstance(db.added[-1], SystemSetting)

    with pytest.raises(ValueError, match="用途无效"):
        device_versions.issue_device_context_token(db, _device(), purpose="invalid")

    db.setting = SimpleNamespace(value="s" * 64)
    db.device = _device()
    token, _expires = device_versions.issue_device_context_token(db, db.device, purpose="launch")
    assert device_versions.verify_device_context_token(db, token, purpose="launch") is db.device
    assert device_versions.verify_device_context_token(db, token, purpose="context") is None
    assert device_versions.verify_device_context_token(db, token + "x", purpose="launch") is None
    db.device.active = False
    assert device_versions.verify_device_context_token(db, token, purpose="launch") is None
    expired, _ = device_versions.issue_device_context_token(db, db.device, lifetime=timedelta(seconds=-1))
    assert device_versions.verify_device_context_token(db, expired) is None
    db.setting = None
    assert device_versions.verify_device_context_token(db, token, purpose="launch") is None
    assert device_versions.verify_device_context_token(db, "not-a-token") is None

    assert device_versions.release_notes_from_manifest(None) == []
    assert device_versions.release_notes_from_manifest({"release_notes": "bad"}) == []
    notes = device_versions.release_notes_from_manifest({"release_notes": ["  第一项  ", "", 2, "x" * 800]})
    assert notes[:2] == ["第一项", "2"] and len(notes[-1]) == 500


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"active": False}, "revoked"),
        ({"status": "revoked"}, "revoked"),
        ({"status": "quarantined"}, "quarantined"),
        ({"app_version": "1.4.3", "agent_version": "1.4.3"}, "current"),
        ({"status": "updating"}, "updating"),
        ({"app_version": ""}, "unknown"),
        ({}, "outdated"),
    ],
)
def test_device_version_state_matrix(monkeypatch, overrides, expected) -> None:
    monkeypatch.setattr(device_versions, "get_settings", lambda: SimpleNamespace(app_version="1.4.3"))
    assert device_versions.device_version_state(_device(**overrides)) == expected


def test_build_gate_messages_and_optional_values(monkeypatch) -> None:
    settings = SimpleNamespace(app_version="1.4.3")
    history = SimpleNamespace(title="正式版", release_notes=["说明"], installed_at="now")
    monkeypatch.setattr(device_versions, "get_settings", lambda: settings)
    monkeypatch.setattr(device_versions, "ensure_current_release", lambda _db: history)
    db = FakeDb()
    unknown = device_versions.build_device_gate(db, None)
    assert unknown["state"] == "host_or_unknown" and unknown["access_allowed"] is True

    package = SimpleNamespace(id="package-1")
    monkeypatch.setattr(device_versions, "latest_device_run", lambda *_a: (None, None))
    monkeypatch.setattr(device_versions, "latest_target_package", lambda _db: None)
    for state, message in (
        ("revoked", "撤销"),
        ("quarantined", "隔离"),
        ("outdated", "尚未保留"),
    ):
        monkeypatch.setattr(device_versions, "device_version_state", lambda _device, value=state: value)
        gate = device_versions.build_device_gate(db, _device(app_version=""))
        assert message in gate["message"] and gate["package_id"] is None

    run = SimpleNamespace(id="run-1", status=UpdateStatus.APPLYING, message="正在更新")
    monkeypatch.setattr(device_versions, "latest_device_run", lambda *_a: (run, package))
    monkeypatch.setattr(device_versions, "latest_target_package", lambda _db: package)
    monkeypatch.setattr(device_versions, "device_version_state", lambda _device: "outdated")
    gate = device_versions.build_device_gate(db, _device())
    assert gate["message"] == "正在更新" and gate["package_id"] == "package-1" and gate["run_id"] == "run-1"

    monkeypatch.setattr(device_versions, "latest_device_run", lambda *_a: (None, None))
    gate = device_versions.build_device_gate(db, _device())
    assert "开始更新" in gate["message"] and gate["package_id"] == "package-1"


def test_start_update_guards_creation_and_command_requeue(monkeypatch) -> None:
    db = FakeDb()
    device = _device()
    monkeypatch.setattr(device_versions, "device_version_state", lambda _device: "current")
    monkeypatch.setattr(device_versions, "latest_device_run", lambda *_a: (SimpleNamespace(id="existing"), None))
    assert device_versions.start_device_update(db, device).id == "existing"
    monkeypatch.setattr(device_versions, "latest_device_run", lambda *_a: (None, None))
    with pytest.raises(ProblemException):
        device_versions.start_device_update(db, device)

    monkeypatch.setattr(device_versions, "device_version_state", lambda _device: "revoked")
    device.active = False
    with pytest.raises(ProblemException):
        device_versions.start_device_update(db, device)
    device.active = True
    device.status = "online"
    monkeypatch.setattr(device_versions, "device_version_state", lambda _device: "outdated")
    monkeypatch.setattr(device_versions, "latest_target_package", lambda _db: None)
    with pytest.raises(ProblemException):
        device_versions.start_device_update(db, device)

    package = SimpleNamespace(
        id="package-1",
        filename="release.partyops-update",
        version="1.4.3",
        created_by="admin",
        manifest={
            "online_download": {"source": "official-online-catalog"}
        },
    )
    monkeypatch.setattr(device_versions, "latest_target_package", lambda _db: package)
    db.scalar_values = [None, None]
    run = device_versions.start_device_update(db, device)
    assert run.status == UpdateStatus.APPLYING and run.progress == 5
    assert len(db.added) == 2 and device.status == "updating"
    assert db.added[1].payload["official_online"] is True
    assert "package_url" not in db.added[1].payload

    existing_run = SimpleNamespace(id="run-2", status=UpdateStatus.FAILED, progress=10, message="")
    command = SimpleNamespace(status="failed", result={"old": True}, completed_at="x", delivered_at="x")
    db.added.clear()
    db.scalar_values = [existing_run, command]
    device.status = "online"
    device.app_version = "1.4.2"
    reused = device_versions.start_device_update(db, device)
    assert reused is existing_run and command.status == "queued" and command.completed_at is None


def test_reconcile_skips_mismatch_and_completes_matching_run(monkeypatch) -> None:
    device = _device(app_version="1.4.3")
    mismatched = SimpleNamespace(package_id="missing")
    matched = SimpleNamespace(package_id="package-1", status=None, progress=0, message="", completed_at=None)
    command = SimpleNamespace(status="queued", result={}, completed_at=None)
    package = SimpleNamespace(id="package-1", version="1.4.3")

    class Rows:
        def all(self):
            return [mismatched, matched]

    class Db(FakeDb):
        def scalars(self, _query):
            return Rows()

        def get(self, model, identity):
            return package if model is UpdatePackage and identity == "package-1" else None

        def scalar(self, _query):
            return command

    monkeypatch.setattr(device_versions, "get_settings", lambda: SimpleNamespace(app_version="1.4.3"))
    db = Db()
    device_versions.reconcile_device_update(db, _device(app_version=""))
    device_versions.reconcile_device_update(db, device)
    assert matched.status == UpdateStatus.COMPLETED and command.status == "completed" and device.status == "online"
