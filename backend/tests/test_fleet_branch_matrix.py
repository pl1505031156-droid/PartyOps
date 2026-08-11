"""跨机传输权限、幂等命令与文件中转辅助分支回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Device, WorkspaceFile, WorkspaceRoot
from app.problems import ProblemException
from app.routers import fleet
from app.security import hash_token


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, objects=None, rows=None, scalars=None) -> None:
        self.objects = objects or {}
        self.rows = rows or []
        self.scalar_values = list(scalars or [])
        self.added = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalars(self, _query):
        return Rows(self.rows)

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def add(self, value) -> None:
        self.added.append(value)


def _transfer(**overrides):
    values = {
        "id": "transfer-1",
        "requested_by": "user-1",
        "delivery_mode": "managed_inbox",
        "direction": "device_to_host",
        "source_file_id": "file-1",
        "source_device_id": "device-1",
        "destination_device_id": None,
        "original_name": "材料.txt",
        "size_bytes": 10,
        "sha256": "a" * 64,
        "chunk_size": 8,
        "total_chunks": 2,
        "completed_chunks": 0,
        "transit_path": "",
        "bundle_mode": "single",
        "item_ids": ["file-1"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_value_cleanup_notification_and_device_count_branches(monkeypatch, tmp_path: Path) -> None:
    admin = SimpleNamespace(id="admin")
    db = Db(rows=[admin], scalars=["exists"])
    fleet.notify_root_approval_needed(db, SimpleNamespace(id="root", name="目录"), SimpleNamespace(name="协同机"))
    assert not db.added

    for value in ("", ".", "x" * (fleet.MAX_FILENAME + 1)):
        with pytest.raises(ProblemException):
            fleet.safe_name(value)
    assert fleet.safe_archive_component(" ... ") == "共享文件"
    assert fleet.normalized_platform("deepin") == "uos"
    assert fleet.normalized_platform("windows11") == "windows"
    assert not fleet.device_is_deleted(SimpleNamespace(device_metadata=None))
    assert fleet.managed_device_count(Db(rows=[SimpleNamespace(device_metadata=None), SimpleNamespace(device_metadata={"deleted_at": "now"})])) == 1

    monkeypatch.setattr(fleet, "get_settings", lambda: SimpleNamespace(max_devices=7, transfers_dir=tmp_path, transfer_quota_gb=1, transfer_max_file_gb=20))
    assert fleet.get_max_devices(Db()) == 7
    assert fleet.get_max_devices(Db(objects={"fleet.max_devices": SimpleNamespace(value=99)})) == 20

    active = SimpleNamespace(active=True, agent_token_hash=hash_token("token"))
    assert not fleet.device_token(SimpleNamespace(active=False, agent_token_hash=active.agent_token_hash), "token")
    assert not fleet.device_token(SimpleNamespace(active=True, agent_token_hash=""), "token")
    assert not fleet.device_token(active, "bad")

    existing = tmp_path / "existing.part"
    existing.write_bytes(b"abc")
    fleet.write_transfer_chunk(existing, 1, b"Z")
    assert existing.read_bytes() == b"aZc"
    monkeypatch.setattr(Path, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(OSError("busy")))
    fleet.cleanup_transfer_part("missing")


def test_transfer_permission_every_revocation_path(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=SimpleNamespace(value="staff"))
    device = SimpleNamespace(
        id="device-1", active=True, status="online",
        allow_host_access=True, allow_device_transfer=True,
    )
    transfer = _transfer()
    assert not fleet.transfer_permission_still_valid(Db(), transfer, "download", None, None)
    assert not fleet.transfer_permission_still_valid(Db(objects={"user-1": user}), transfer, "download", "device-1", None)
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device}), transfer, "download", "device-1", None)
    device.active = False
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "download", "device-1", None)
    device.active = True
    device.status = "revoked"
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "download", "device-1", None)
    device.status = "online"
    device.allow_host_access = False
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "download", "device-1", None)

    device.allow_host_access = True
    transfer.delivery_mode = "current_device"
    transfer.destination_device_id = "device-1"
    assert fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "upload", "device-1", None)
    transfer.delivery_mode = "managed_inbox"
    transfer.destination_device_id = None
    assert fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "upload", "device-1", None)
    device.allow_device_transfer = False
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "share", "device-1", "root-1")
    monkeypatch.setattr(fleet, "grant_allows", lambda *_a, **_k: False)
    assert not fleet.transfer_permission_still_valid(Db(objects={(Device, "device-1"): device, "user-1": user}), transfer, "download", "device-1", "root-1")


def test_queue_transfer_commands_existing_failed_bundle_and_noop(monkeypatch) -> None:
    source = SimpleNamespace(
        id="file-1", remote_file_key="device-1:root:材料.txt", modified_at=fleet.utcnow(),
        relative_path="材料.txt", name="材料.txt", is_directory=False, root_id="root-1",
    )
    bundle_item = SimpleNamespace(
        id="file-2", remote_file_key="device-1:root:目录", relative_path="目录",
        name="目录", is_directory=True,
    )
    monkeypatch.setattr(fleet, "get_settings", lambda: SimpleNamespace(transfer_max_file_gb=20))

    failed = SimpleNamespace(status="failed", result={"old": True}, delivered_at="x", completed_at="x")
    db = Db(objects={(WorkspaceFile, "file-1"): source, (WorkspaceFile, "file-2"): bundle_item}, scalars=[failed])
    transfer = _transfer(bundle_mode="selection_zip", sha256="", item_ids=["missing", "file-2"])
    fleet.queue_transfer_commands(db, transfer)
    assert failed.status == "queued" and failed.result == {} and not db.added

    completed = SimpleNamespace(status="completed")
    db = Db(objects={(WorkspaceFile, "file-1"): source}, scalars=[completed])
    fleet.queue_transfer_commands(db, _transfer())
    assert completed.status == "completed" and not db.added

    db = Db(objects={(WorkspaceFile, "file-1"): source})
    fleet.queue_transfer_commands(db, _transfer(direction="host_to_device", source_device_id=None, destination_device_id=None, total_chunks=2, completed_chunks=2))
    assert not db.added
    fleet.queue_transfer_commands(db, _transfer(direction="host_to_device", source_device_id=None, destination_device_id="device-2", total_chunks=2, completed_chunks=2))
    assert db.added[-1].command_type == "download_file"


@pytest.mark.parametrize(
    "mutation",
    ["no_user", "no_ids", "missing_item", "missing_root", "host_root", "wrong_device", "denied"],
)
def test_transfer_sources_revalidate_every_source(mutation, monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    item = SimpleNamespace(id="file-1", root_id="root-1")
    root = SimpleNamespace(id="root-1", enabled=True, source=SimpleNamespace(value="device"), device_id="device-1")
    objects = {"user-1": user, (WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root}
    transfer = _transfer()
    allowed = True
    if mutation == "no_user":
        objects.pop("user-1")
    elif mutation == "no_ids":
        transfer.item_ids = []
        transfer.source_file_id = None
    elif mutation == "missing_item":
        objects.pop((WorkspaceFile, "file-1"))
    elif mutation == "missing_root":
        objects.pop((WorkspaceRoot, "root-1"))
    elif mutation == "host_root":
        root.source = SimpleNamespace(value="host")
    elif mutation == "wrong_device":
        root.device_id = "device-2"
    elif mutation == "denied":
        allowed = False
    monkeypatch.setattr(fleet, "workspace_root_permissions", lambda *_a, **_k: {"download": allowed})
    assert not fleet.transfer_sources_still_allowed(Db(objects=objects), transfer)


def test_transfer_source_root_empty_and_storage_success(monkeypatch, tmp_path: Path) -> None:
    assert fleet.transfer_source_root(Db(), _transfer(source_file_id=None)) is None
    assert fleet.transfer_source_root(Db(), _transfer(source_file_id="missing")) is None
    settings = SimpleNamespace(transfers_dir=tmp_path, transfer_quota_gb=1)
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)
    monkeypatch.setattr(fleet.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1024))
    fleet.ensure_transfer_storage_available(10)
