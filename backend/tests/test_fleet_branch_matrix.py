"""跨机传输权限、幂等命令与文件中转辅助分支回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Device, DeviceCommand, DeviceEnrollment, DeviceGrant, SystemSetting, Transfer, UpdateRun, User, WorkspaceFile, WorkspaceRoot
from app.problems import ProblemException
from app.routers import fleet
from app.schemas import DeviceGrantCreate, DevicePatch, RemoteRootPatch, RemoteRootRequest
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

    def commit(self) -> None:
        return None

    def refresh(self, _value) -> None:
        return None

    def flush(self) -> None:
        return None

    def execute(self, _statement):
        return None


class SequenceDb(Db):
    """为一个端点中的多次 scalars 查询依次返回预置结果。"""

    def __init__(self, row_groups) -> None:
        super().__init__()
        self.row_groups = list(row_groups)

    def scalars(self, _query):
        return Rows(self.row_groups.pop(0))


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


def test_collaboration_options_filters_roots_devices_and_absolute_paths(monkeypatch) -> None:
    current = SimpleNamespace(
        id="device-current",
        name="当前电脑",
        status="online",
        active=True,
        allow_host_access=True,
        allow_device_transfer=False,
        allow_user_shares=True,
    )
    allowed = SimpleNamespace(
        id="device-allowed",
        name="获批电脑",
        status=SimpleNamespace(value="offline"),
        active=True,
        allow_host_access=True,
        allow_device_transfer=False,
        allow_user_shares=False,
    )
    transferable = SimpleNamespace(
        id="device-transfer",
        name="允许互传",
        status="online",
        active=True,
        allow_host_access=True,
        allow_device_transfer=True,
        allow_user_shares=True,
    )
    hidden = SimpleNamespace(
        id="device-hidden",
        name="不可见电脑",
        status="offline",
        active=True,
        allow_host_access=False,
        allow_device_transfer=False,
        allow_user_shares=False,
    )
    visible_root = SimpleNamespace(
        id="root-visible",
        name="公开目录",
        source=SimpleNamespace(value="device"),
        device_id=allowed.id,
        remote_key="share_1",
        approval_status="approved",
        approval_note="",
        published_by_user_id="user-1",
        share_scope="team",
        semantic_content_enabled=False,
        enabled=True,
    )
    hidden_root = SimpleNamespace(**{**visible_root.__dict__, "id": "root-hidden", "name": "隐藏目录", "device_id": hidden.id})
    permissions = {
        visible_root.id: {"browse": True, "manage_root": False},
        hidden_root.id: {"browse": False, "manage_root": False},
    }
    monkeypatch.setattr(fleet, "request_device", lambda *_args: current)
    monkeypatch.setattr(
        fleet,
        "workspace_root_permissions",
        lambda _db, root, *_args: permissions[root.id],
    )
    staff = SimpleNamespace(id="user-1", role=SimpleNamespace(value="staff"))
    result = fleet.collaboration_options(
        SimpleNamespace(),
        staff,
        SequenceDb([[current, allowed, transferable, hidden], [visible_root, hidden_root]]),
    )
    assert result["current_device"]["id"] == current.id
    assert {item["id"] for item in result["devices"]} == {
        current.id,
        allowed.id,
        transferable.id,
    }
    assert result["roots"] == [
        {
            "id": visible_root.id,
            "name": visible_root.name,
            "source": "device",
            "device_id": allowed.id,
            "remote_key": "share_1",
            "approval_status": "approved",
            "approval_note": "",
            "published_by_user_id": "user-1",
            "share_scope": "team",
            "semantic_content_enabled": False,
            "enabled": True,
            "permissions": permissions[visible_root.id],
        }
    ]
    # 该响应只公开远程键和授权状态，绝不能泄露共享端的本机绝对路径。
    assert "local_path" not in result["roots"][0]

    monkeypatch.setattr(fleet, "request_device", lambda *_args: None)
    admin = SimpleNamespace(id="admin", role=SimpleNamespace(value="admin"))
    admin_result = fleet.collaboration_options(
        SimpleNamespace(),
        admin,
        SequenceDb([[current, hidden], []]),
    )
    assert admin_result["current_device"] is None
    assert {item["id"] for item in admin_result["devices"]} == {current.id, hidden.id}


def test_ack_command_covers_transfer_success_failure_dedup_and_update_result(monkeypatch) -> None:
    device = SimpleNamespace(id="device-1")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_args: device)
    cleaned: list[str] = []
    monkeypatch.setattr(fleet, "cleanup_transfer_part", cleaned.append)

    with pytest.raises(ProblemException) as missing:
        fleet.ack_command("missing", {"ok": True}, "token", Db())
    assert missing.value.code == "DEVICE_COMMAND_NOT_FOUND"

    foreign = SimpleNamespace(id="foreign", device_id="other")
    with pytest.raises(ProblemException) as wrong_device:
        fleet.ack_command(
            foreign.id,
            {"ok": True},
            "token",
            Db(objects={(DeviceCommand, foreign.id): foreign}),
        )
    assert wrong_device.value.code == "DEVICE_COMMAND_NOT_FOUND"

    transfer = SimpleNamespace(
        id="transfer-1",
        requested_by="user-1",
        original_name="材料.zip",
        status="transferring",
        completed_chunks=0,
        total_chunks=3,
        error_code="OLD",
        error_message="old",
        version=0,
    )
    download = SimpleNamespace(
        id="download",
        device_id=device.id,
        command_type="download_file",
        payload={"transfer_id": transfer.id},
        status="delivered",
        result={},
        completed_at=None,
    )
    success_db = Db(
        objects={
            (DeviceCommand, download.id): download,
            (Transfer, transfer.id): transfer,
        },
        scalars=[None],
    )
    assert fleet.ack_command(download.id, {"ok": True, "message": "完成"}, "token", success_db) == {"acknowledged": True}
    assert transfer.status == "completed" and transfer.completed_chunks == 3
    assert transfer.error_code == "" and cleaned == [transfer.id]
    assert success_db.added and success_db.added[0].title == "文件传输完成"

    transfer.status = "transferring"
    upload = SimpleNamespace(
        id="upload",
        device_id=device.id,
        command_type="upload_file",
        payload={"transfer_id": transfer.id},
        status="delivered",
        result={},
        completed_at=None,
    )
    dedup_db = Db(
        objects={(DeviceCommand, upload.id): upload, (Transfer, transfer.id): transfer},
        scalars=["existing-notification"],
    )
    fleet.ack_command(upload.id, {"ok": True}, "token", dedup_db)
    assert not dedup_db.added and cleaned == [transfer.id]

    update_run = SimpleNamespace(status="applying", progress=50, message="", completed_at=None)
    failed = SimpleNamespace(
        id="failed-update",
        device_id=device.id,
        command_type="apply_update",
        payload={"run_id": "run-1"},
        status="delivered",
        result={},
        completed_at=None,
    )
    failed_db = Db(
        objects={
            (DeviceCommand, failed.id): failed,
            (UpdateRun, "run-1"): update_run,
        }
    )
    fleet.ack_command(
        failed.id,
        {"ok": False, "error_code": "UPDATE_INSTALL_FAILED", "message": "安装未完成"},
        "token",
        failed_db,
    )
    assert failed.status == "failed"
    assert update_run.status == "failed" and update_run.progress == 0
    assert update_run.message == "安装未完成"


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


def test_device_admin_configuration_status_patch_and_rotation_matrix(monkeypatch) -> None:
    request = SimpleNamespace(client=None)
    admin = SimpleNamespace(id="admin")
    monkeypatch.setattr(fleet, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fleet, "managed_device_count", lambda _db: 2)

    with pytest.raises(ProblemException) as invalid_limit:
        fleet.update_fleet_config(0, request, admin, Db())
    assert invalid_limit.value.code == "MAX_DEVICES_INVALID"
    created_db = Db()
    assert fleet.update_fleet_config(8, request, admin, created_db)["max_devices"] == 8
    assert isinstance(created_db.added[0], SystemSetting)
    existing = SimpleNamespace(value=3)
    existing_db = Db(objects={(SystemSetting, "fleet.max_devices"): existing})
    fleet.update_fleet_config(9, request, admin, existing_db)
    assert existing.value == 9

    with pytest.raises(ProblemException) as missing_enrollment:
        fleet.enrollment_status("missing", admin, Db())
    assert missing_enrollment.value.code == "ENROLLMENT_NOT_FOUND"
    enrollment = SimpleNamespace(id="enrollment", expires_at=fleet.utcnow(), used_at=None)
    device = SimpleNamespace(id="device", name="终端", status="online", last_seen_at=None)
    monkeypatch.setattr(fleet, "enrollment_device", lambda _db, _id: device)
    assert fleet.enrollment_status("enrollment", admin, Db(objects={(DeviceEnrollment, "enrollment"): enrollment})).status == "enrolled"
    monkeypatch.setattr(fleet, "enrollment_device", lambda _db, _id: None)
    assert fleet.enrollment_status("enrollment", admin, Db(objects={(DeviceEnrollment, "enrollment"): enrollment})).status == "expired"
    enrollment.expires_at = fleet.utcnow() + fleet.timedelta(minutes=5)
    assert fleet.enrollment_status("enrollment", admin, Db(objects={(DeviceEnrollment, "enrollment"): enrollment})).status == "pending"

    with pytest.raises(ProblemException) as missing_device:
        fleet.patch_device("missing", DevicePatch(active=False), request, "1", admin, Db())
    assert missing_device.value.code == "DEVICE_NOT_FOUND"
    managed = SimpleNamespace(
        id="device", version=2, active=True, status="online", device_metadata={}
    )
    db = Db(objects={(Device, "device"): managed})
    with pytest.raises(ProblemException) as conflict:
        fleet.patch_device("device", DevicePatch(active=False), request, "1", admin, db)
    assert conflict.value.code == "VERSION_CONFLICT"
    fleet.patch_device("device", DevicePatch(active=False), request, "2", admin, db)
    assert managed.status == "revoked"
    fleet.patch_device("device", DevicePatch(active=True), request, "3", admin, db)
    assert managed.status == "offline"

    with pytest.raises(ProblemException):
        fleet.rotate_device_token("missing", request, admin, Db())
    rotated = fleet.rotate_device_token("device", request, admin, db)
    assert rotated["device_id"] == "device" and managed.agent_token_hash
    managed.active = False
    with pytest.raises(ProblemException):
        fleet.queue_certificate_rotation("device", request, admin, db)
    managed.active = True
    queued = fleet.queue_certificate_rotation("device", request, admin, db)
    assert queued["device_id"] == "device"


def test_device_grant_validation_and_remote_root_lifecycle(monkeypatch) -> None:
    request = SimpleNamespace(client=None)
    admin = SimpleNamespace(id="admin")
    monkeypatch.setattr(fleet, "write_audit", lambda *_args, **_kwargs: None)
    device = SimpleNamespace(id="device", active=True, device_metadata={})

    def create(payload: DeviceGrantCreate, db: Db):
        return fleet.create_device_grant(payload, request, admin, db)

    with pytest.raises(ProblemException) as missing_device:
        create(DeviceGrantCreate(device_id="missing", capabilities=["download"]), Db())
    assert missing_device.value.code == "DEVICE_NOT_FOUND"
    inactive = SimpleNamespace(id="device", active=False)
    with pytest.raises(ProblemException):
        create(DeviceGrantCreate(device_id="device", capabilities=["download"]), Db(objects={(Device, "device"): inactive}))
    for capabilities in ([], ["unknown"]):
        with pytest.raises(ProblemException) as invalid:
            create(DeviceGrantCreate(device_id="device", capabilities=capabilities), Db(objects={(Device, "device"): device}))
        assert invalid.value.code == "DEVICE_GRANT_CAPABILITY_INVALID"
    with pytest.raises(ProblemException) as missing_user:
        create(
            DeviceGrantCreate(device_id="device", user_id="missing", capabilities=["download"]),
            Db(objects={(Device, "device"): device}),
        )
    assert missing_user.value.code == "USER_NOT_FOUND"
    user = SimpleNamespace(id="user")
    with pytest.raises(ProblemException) as missing_root:
        create(
            DeviceGrantCreate(device_id="device", user_id="user", root_id="missing", capabilities=["download"]),
            Db(objects={(Device, "device"): device, (User, "user"): user}),
        )
    assert missing_root.value.code == "WORKSPACE_ROOT_NOT_FOUND"

    for changes in (
        {"enabled": False},
        {"approval_status": "pending"},
        {"device_id": "other"},
    ):
        root = SimpleNamespace(
            id="root", enabled=True, approval_status="approved",
            source=SimpleNamespace(value="device"), device_id="device",
        )
        for key, value in changes.items():
            setattr(root, key, value)
        with pytest.raises(ProblemException) as invalid_root:
            create(
                DeviceGrantCreate(device_id="device", root_id="root", capabilities=["download"]),
                Db(objects={(Device, "device"): device, (WorkspaceRoot, "root"): root}),
            )
        assert invalid_root.value.code == "DEVICE_GRANT_ROOT_INVALID"

    with pytest.raises(ProblemException):
        fleet.patch_device_grant("missing", True, request, "1", admin, Db())
    grant = SimpleNamespace(id="grant", version=2, active=True)
    grant_db = Db(objects={(DeviceGrant, "grant"): grant})
    with pytest.raises(ProblemException):
        fleet.patch_device_grant("grant", True, request, "1", admin, grant_db)
    fleet.patch_device_grant("grant", False, request, "2", admin, grant_db)
    assert not grant.active and grant.version == 3

    missing_device_db = Db()
    payload = RemoteRootRequest(device_id="device", name="共享目录", remote_key="safe-key")
    with pytest.raises(ProblemException):
        fleet.request_remote_root(payload, request, admin, missing_device_db)
    with pytest.raises(ProblemException) as unsafe:
        fleet.request_remote_root(
            RemoteRootRequest(device_id="device", name="共享目录", remote_key="../escape"),
            request,
            admin,
            Db(objects={(Device, "device"): device}),
        )
    assert unsafe.value.code == "REMOTE_PATH_INVALID"
    existing = SimpleNamespace(
        id="root", name="已有目录", source=SimpleNamespace(value="device"),
        device_id="device", remote_key="safe-key", approval_status="approved",
        enabled=True, version=1,
    )
    existing_result = fleet.request_remote_root(
        payload,
        request,
        admin,
        Db(objects={(Device, "device"): device}, scalars=[existing]),
    )
    assert not existing_result["created"]

    with pytest.raises(ProblemException):
        fleet.patch_remote_root("missing", RemoteRootPatch(enabled=True), request, "1", admin, Db())
    root = SimpleNamespace(
        id="root", source=SimpleNamespace(value="device"), version=2,
        enabled=False, approval_status="pending", approval_note="",
    )
    root_db = Db(objects={(WorkspaceRoot, "root"): root})
    with pytest.raises(ProblemException):
        fleet.patch_remote_root("root", RemoteRootPatch(enabled=True), request, "1", admin, root_db)
    fleet.patch_remote_root("root", RemoteRootPatch(approval_status="approved"), request, "2", admin, root_db)
    assert root.enabled
    fleet.patch_remote_root("root", RemoteRootPatch(approval_status="rejected"), request, "3", admin, root_db)
    assert not root.enabled
    with pytest.raises(ProblemException) as invalid_status:
        fleet.list_admin_remote_roots("invalid", admin, Db())
    assert invalid_status.value.code == "REMOTE_ROOT_STATUS_INVALID"
    assert fleet.list_admin_remote_roots(None, admin, Db(rows=[])) == []


def test_transfer_actions_and_bundle_preparation_fail_closed(monkeypatch, tmp_path: Path) -> None:
    request = SimpleNamespace(client=None)
    staff = SimpleNamespace(id="staff", role=SimpleNamespace(value="staff"))
    admin = SimpleNamespace(id="admin", role=SimpleNamespace(value="admin"))
    monkeypatch.setattr(fleet, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fleet, "queue_transfer_commands", lambda *_args: None)
    monkeypatch.setattr(fleet, "cleanup_transfer_part", lambda *_args: None)

    with pytest.raises(ProblemException) as missing:
        fleet.action_transfer("missing", SimpleNamespace(action="cancel", note=""), request, "1", admin, Db())
    assert missing.value.code == "TRANSFER_NOT_FOUND"
    transfer = SimpleNamespace(
        id="transfer", requested_by="owner", version=2, status="queued",
        source_device_id=None, completed_chunks=1, transit_path="part",
        error_code="old", error_message="old", approved_by=None, approval_note="",
    )
    db = Db(objects={(Transfer, "transfer"): transfer})
    with pytest.raises(ProblemException) as forbidden:
        fleet.action_transfer("transfer", SimpleNamespace(action="cancel", note=""), request, "2", staff, db)
    assert forbidden.value.code == "TRANSFER_FORBIDDEN"
    transfer.requested_by = staff.id
    with pytest.raises(ProblemException) as conflict:
        fleet.action_transfer("transfer", SimpleNamespace(action="cancel", note=""), request, "1", staff, db)
    assert conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as approval:
        fleet.action_transfer("transfer", SimpleNamespace(action="approve", note=""), request, "2", staff, db)
    assert approval.value.code == "ADMIN_APPROVAL_REQUIRED"

    transfer.status = "failed"
    transfer.version = 2
    fleet.action_transfer("transfer", SimpleNamespace(action="retry", note="重试"), request, "2", staff, db)
    assert transfer.status == "queued" and transfer.error_code == ""
    transfer.status = "failed"
    transfer.source_device_id = "device"
    fleet.action_transfer("transfer", SimpleNamespace(action="retry", note="重试"), request, "3", staff, db)
    assert transfer.completed_chunks == 0 and transfer.transit_path == ""

    device = SimpleNamespace(id="device")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_args: device)
    monkeypatch.setattr(fleet, "get_settings", lambda: SimpleNamespace(transfer_max_file_gb=1))
    with pytest.raises(ProblemException) as unavailable:
        fleet.prepare_device_bundle("missing", {}, "token", Db())
    assert unavailable.value.code == "TRANSFER_NOT_FOUND"
    bundle = SimpleNamespace(
        id="bundle", source_device_id="device", bundle_mode="selection_zip",
        status="queued", completed_chunks=0, size_bytes=0, sha256="",
        chunk_size=8, total_chunks=0, version=0,
    )
    bundle_db = Db(objects={(Transfer, "bundle"): bundle})
    monkeypatch.setattr(fleet, "transfer_sources_still_allowed", lambda *_args: False)
    with pytest.raises(ProblemException) as revoked:
        fleet.prepare_device_bundle("bundle", {"size_bytes": 0, "sha256": "0" * 64}, "token", bundle_db)
    assert revoked.value.code == "GRANT_DENIED"
    monkeypatch.setattr(fleet, "transfer_sources_still_allowed", lambda *_args: True)
    with pytest.raises(ProblemException) as too_large:
        fleet.prepare_device_bundle("bundle", {"size_bytes": 2 * 1024**3, "sha256": "0" * 64}, "token", bundle_db)
    assert too_large.value.code == "TRANSFER_FILE_TOO_LARGE"
    with pytest.raises(ProblemException) as bad_hash:
        fleet.prepare_device_bundle("bundle", {"size_bytes": 1, "sha256": "bad"}, "token", bundle_db)
    assert bad_hash.value.code == "BUNDLE_HASH_INVALID"
    bundle.completed_chunks = 1
    bundle.size_bytes = 8
    bundle.sha256 = "a" * 64
    with pytest.raises(ProblemException) as changed:
        fleet.prepare_device_bundle("bundle", {"size_bytes": 9, "sha256": "b" * 64}, "token", bundle_db)
    assert changed.value.code == "BUNDLE_CHANGED"


def test_device_transfer_status_finalize_and_download_guards(monkeypatch, tmp_path: Path) -> None:
    device = SimpleNamespace(id="device")
    monkeypatch.setattr(fleet, "authenticated_device", lambda *_args: device)
    settings = SimpleNamespace(transfers_dir=tmp_path, inbox_dir=tmp_path / "inbox")
    settings.inbox_dir.mkdir()
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)
    monkeypatch.setattr(fleet, "cleanup_transfer_part", lambda *_args: None)
    monkeypatch.setattr(fleet, "queue_transfer_commands", lambda *_args: None)

    with pytest.raises(ProblemException):
        fleet.device_transfer_status("missing", "token", Db())
    transfer = SimpleNamespace(
        id="transfer", source_device_id="device", destination_device_id=None,
        status="transferring", original_name="材料.txt", size_bytes=0,
        sha256="", chunk_size=8, total_chunks=0, completed_chunks=0,
        expires_at=fleet.utcnow() + fleet.timedelta(minutes=5), error_code="",
        transit_path="", result_sha256="", direction="device_to_host", version=0,
    )
    status_db = Db(objects={(Transfer, "transfer"): transfer}, rows=[0, 2])
    assert fleet.device_transfer_status("transfer", "token", status_db)["completed_chunks"] == [0, 2]

    monkeypatch.setattr(fleet, "transfer_sources_still_allowed", lambda *_args: False)
    with pytest.raises(ProblemException) as revoked:
        fleet.finalize_device_upload("transfer", "token", status_db)
    assert revoked.value.code == "GRANT_DENIED"
    monkeypatch.setattr(fleet, "transfer_sources_still_allowed", lambda *_args: True)
    result = fleet.finalize_device_upload("transfer", "token", status_db)
    assert result["status"] == "completed"
    assert any(settings.inbox_dir.iterdir())

    failed = SimpleNamespace(**{**transfer.__dict__})
    failed.id = "failed"
    failed.size_bytes = 3
    failed.total_chunks = 1
    failed.completed_chunks = 1
    failed.sha256 = "0" * 64
    part = tmp_path / "failed.part"
    part.write_bytes(b"abc")
    failed_db = Db(objects={(Transfer, "failed"): failed})
    with pytest.raises(ProblemException) as hash_mismatch:
        fleet.finalize_device_upload("failed", "token", failed_db)
    assert hash_mismatch.value.code == "HASH_MISMATCH" and failed.status == "failed"

    incomplete = SimpleNamespace(**{**failed.__dict__})
    incomplete.id = "incomplete"
    incomplete.status = "transferring"
    incomplete.sha256 = ""
    incomplete.completed_chunks = 0
    with pytest.raises(ProblemException) as incomplete_error:
        fleet.finalize_device_upload("incomplete", "token", Db(objects={(Transfer, "incomplete"): incomplete}))
    assert incomplete_error.value.code == "TRANSFER_INCOMPLETE"

    download = SimpleNamespace(
        id="download", destination_device_id="device", source_device_id=None,
        status="queued", expires_at=fleet.utcnow() + fleet.timedelta(minutes=5),
        completed_chunks=0, transit_path="", chunk_size=2, destination_root_id=None,
    )
    download_db = Db(objects={(Transfer, "download"): download})
    monkeypatch.setattr(fleet, "transfer_permission_still_valid", lambda *_args: True)
    with pytest.raises(ProblemException) as not_ready:
        fleet.download_chunk("download", 0, "token", download_db)
    assert not_ready.value.code == "CHUNK_NOT_READY"
    (tmp_path / "download.part").write_bytes(b"abcd")
    with pytest.raises(ProblemException):
        fleet.download_chunk("download", 3, "token", download_db)
    response = fleet.download_chunk("download", 1, "token", download_db)
    assert response.body == b"cd"
    monkeypatch.setattr(fleet, "transfer_permission_still_valid", lambda *_args: False)
    with pytest.raises(ProblemException) as denied:
        fleet.download_chunk("download", 0, "token", download_db)
    assert denied.value.code == "GRANT_DENIED" and download.status == "paused"
