from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Device, WorkspaceFile, WorkspaceRoot
from app.problems import ProblemException
from app.routers import fleet
from app.schemas import DeviceEnrollRequest
from app.security import hash_token


class _Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return list(self.values)


class _Db:
    def __init__(self, *, objects=None, scalar_value=None, rows=None) -> None:
        self.objects = objects or {}
        self.scalar_value = scalar_value
        self.rows = rows or []
        self.added = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalar(self, _statement):
        return self.scalar_value

    def scalars(self, _statement):
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)


def test_fleet_value_guards_enrollment_and_authentication(monkeypatch) -> None:
    assert fleet.client_ip(SimpleNamespace(client=SimpleNamespace(host="192.168.1.8"))) == "192.168.1.8"
    assert fleet.client_ip(SimpleNamespace(client=None)) == ""
    assert fleet.parse_version('"12"') == 12
    with pytest.raises(ProblemException) as missing_version:
        fleet.parse_version(None)
    assert missing_version.value.code == "IF_MATCH_REQUIRED"
    with pytest.raises(ProblemException) as invalid_version:
        fleet.parse_version("bad")
    assert invalid_version.value.code == "IF_MATCH_INVALID"
    assert fleet.safe_name("folder/材料.txt") == "材料.txt"
    for value in ("..", "bad\x00name"):
        with pytest.raises(ProblemException):
            fleet.safe_name(value)
    assert fleet.safe_archive_component("../../异常\\目录:名称")
    assert fleet.enum_value(SimpleNamespace(value="queued")) == "queued"
    assert fleet.normalized_platform("Linux-UOS") == "uos"
    assert fleet.normalized_platform("win32") == "windows"
    assert fleet.normalized_platform("Other-Platform") == "other-platform"

    payload = DeviceEnrollRequest(
        code="enroll-code",
        name="协同机",
        architecture="amd64",
        platform="windows",
        kernel="10",
        app_version="1.4.2",
        agent_version="1.4.2",
    )
    assert fleet.enrollment_request_fingerprint("code", payload) == fleet.enrollment_request_fingerprint("code", payload)
    enrolled = SimpleNamespace(active=True, device_metadata={"enrollment_id": "enroll-1"})
    assert fleet.enrollment_device(_Db(rows=[enrolled]), "enroll-1") is enrolled
    assert fleet.enrollment_device(_Db(rows=[]), "enroll-1") is None

    active = SimpleNamespace(active=True, agent_token_hash=hash_token("token"), status="online")
    assert fleet.device_token(active, "token")
    with pytest.raises(ProblemException) as missing_token:
        fleet.authenticated_device(None, _Db())
    assert missing_token.value.code == "DEVICE_TOKEN_REQUIRED"
    with pytest.raises(ProblemException) as invalid_token:
        fleet.authenticated_device("bad", _Db(scalar_value=None))
    assert invalid_token.value.code == "DEVICE_TOKEN_INVALID"
    blocked = SimpleNamespace(active=True, agent_token_hash=hash_token("token"), status="quarantined")
    with pytest.raises(ProblemException) as blocked_error:
        fleet.authenticated_device("token", _Db(scalar_value=blocked))
    assert blocked_error.value.code == "DEVICE_BLOCKED"
    assert fleet.authenticated_device("token", _Db(scalar_value=active)) is active


def test_notifications_counts_permissions_and_transfer_commands(monkeypatch) -> None:
    admin = SimpleNamespace(id="admin-1")
    root = SimpleNamespace(id="root-1", name="共享目录")
    device = SimpleNamespace(id="device-1", name="协同机")
    notification_db = _Db(rows=[admin])
    fleet.notify_root_approval_needed(notification_db, root, device)
    assert notification_db.added and notification_db.added[0].notification_type == "root_approval"

    deleted = SimpleNamespace(device_metadata={"deleted_at": "2026-08-11"})
    active = SimpleNamespace(device_metadata={})
    assert fleet.device_is_deleted(deleted)
    assert fleet.managed_device_count(_Db(rows=[deleted, active])) == 1
    monkeypatch.setattr(fleet, "get_settings", lambda: SimpleNamespace(max_devices=25))
    assert fleet.get_max_devices(_Db(objects={"fleet.max_devices": SimpleNamespace(value="0")})) == 1
    assert fleet.get_max_devices(_Db(objects={"fleet.max_devices": SimpleNamespace(value="bad")})) == 20

    staff = SimpleNamespace(id="user-1", role=SimpleNamespace(value="staff"))
    admin_user = SimpleNamespace(id="admin-1", role=SimpleNamespace(value="admin"))
    transfer = SimpleNamespace(requested_by="user-1", delivery_mode="managed_inbox", destination_device_id=None)
    device = SimpleNamespace(id="device-1", active=True, status="online", allow_host_access=False, allow_device_transfer=False)
    db = _Db(objects={(Device, "device-1"): device, "user-1": staff})
    assert not fleet.transfer_permission_still_valid(db, transfer, "download", "device-1", "root-1")
    db.objects["user-1"] = admin_user
    monkeypatch.setattr(fleet, "grant_allows", lambda *_a, **_k: True)
    assert fleet.transfer_permission_still_valid(db, transfer, "download", "device-1", "root-1")
    transfer.delivery_mode = "current_device"
    transfer.destination_device_id = "device-1"
    assert fleet.transfer_permission_still_valid(db, transfer, "upload", "device-1", None)
    transfer.delivery_mode = "managed_inbox"
    device.allow_device_transfer = True
    assert fleet.transfer_permission_still_valid(db, transfer, "upload", "device-1", None)

    source = SimpleNamespace(
        id="file-1", remote_file_key="device-1:share:材料.txt", modified_at=None,
        relative_path="材料.txt", name="材料.txt", is_directory=False, root_id="root-1",
    )
    upload_transfer = SimpleNamespace(
        id="transfer-1", direction="device_to_host", source_file_id="file-1", source_device_id="device-1",
        destination_device_id=None, original_name="材料.txt", size_bytes=10, sha256="a" * 64,
        chunk_size=8, total_chunks=2, completed_chunks=0, transit_path="", bundle_mode="single", item_ids=["file-1"],
    )
    command_db = _Db(objects={(WorkspaceFile, "file-1"): source})
    monkeypatch.setattr(fleet, "get_settings", lambda: SimpleNamespace(transfer_max_file_gb=20))
    fleet.queue_transfer_commands(command_db, upload_transfer)
    assert command_db.added[0].command_type == "upload_file"

    destination_transfer = SimpleNamespace(**{**upload_transfer.__dict__, "id": "transfer-2", "direction": "host_to_device", "source_device_id": None, "destination_device_id": "device-2", "total_chunks": 1, "completed_chunks": 1})
    destination_db = _Db(objects={(WorkspaceFile, "file-1"): source})
    fleet.queue_transfer_commands(destination_db, destination_transfer)
    assert destination_db.added[0].command_type == "download_file"

    bundle_transfer = SimpleNamespace(**{**upload_transfer.__dict__, "id": "transfer-3", "bundle_mode": "selection_zip", "sha256": "", "item_ids": ["file-1"]})
    bundle_db = _Db(objects={(WorkspaceFile, "file-1"): source})
    fleet.queue_transfer_commands(bundle_db, bundle_transfer)
    assert bundle_db.added[0].command_type == "upload_bundle"


def test_transfer_source_permissions_and_storage_limits(monkeypatch, tmp_path: Path) -> None:
    user = SimpleNamespace(id="user-1")
    item = SimpleNamespace(id="file-1", root_id="root-1")
    root = SimpleNamespace(id="root-1", enabled=True, source=SimpleNamespace(value="device"), device_id="device-1")
    transfer = SimpleNamespace(requested_by="user-1", item_ids=["file-1"], source_file_id="file-1", source_device_id="device-1")
    db = _Db(objects={"user-1": user, (WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root})
    monkeypatch.setattr(fleet, "workspace_root_permissions", lambda *_a, **_k: {"download": True})
    assert fleet.transfer_sources_still_allowed(db, transfer)
    root.enabled = False
    assert not fleet.transfer_sources_still_allowed(db, transfer)
    assert fleet.transfer_source_root(db, transfer) is root

    transfers_dir = tmp_path / "transfers"
    transfers_dir.mkdir()
    part = transfers_dir / "one.part"
    part.write_bytes(b"12345")
    settings = SimpleNamespace(transfers_dir=transfers_dir, transfer_quota_gb=0)
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)
    monkeypatch.setattr(fleet.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100))
    with pytest.raises(ProblemException) as quota:
        fleet.ensure_transfer_storage_available(1)
    assert quota.value.code == "TRANSFER_QUOTA_EXCEEDED"
    settings.transfer_quota_gb = 1
    monkeypatch.setattr(fleet.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    with pytest.raises(ProblemException) as disk:
        fleet.ensure_transfer_storage_available(1)
    assert disk.value.code == "DISK_FULL"

    target = tmp_path / "chunk.part"
    fleet.write_transfer_chunk(target, 2, b"abc")
    assert target.read_bytes() == b"\x00\x00abc"
    source = tmp_path / "hash.txt"
    source.write_bytes(b"hash")
    assert fleet.sha256_path(source)
