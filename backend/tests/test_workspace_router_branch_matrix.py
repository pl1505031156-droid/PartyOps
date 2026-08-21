"""工作区路由的运行位置、授权、预览与本机打开边界回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import UserRole
from app.models import PeriodReport, Task, WorkJournalEntry, WorkspaceFile, WorkspaceLink, WorkspaceRoot
from app.problems import ProblemException
from app.routers import workspace
from app.schemas import WorkspaceFileLinkCreate


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, objects=None, rows=None, scalar_values=None) -> None:
        self.objects = objects or {}
        self.rows = rows or []
        self.scalar_values = list(scalar_values or [])
        self.deleted = []

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalars(self, _query):
        return Rows(self.rows)

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def delete(self, value) -> None:
        self.deleted.append(value)

    def commit(self) -> None:
        return None


def _request(host: str | None = "127.0.0.1"):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host is not None else None, cookies={})


def _root(source="host", **overrides):
    values = {"id": "root-1", "enabled": True, "source": SimpleNamespace(value=source), "device_id": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def _item(**overrides):
    values = {
        "id": "file-1", "root_id": "root-1", "in_scope": True, "is_directory": False,
        "relative_path": "通知.txt", "name": "通知.txt", "mime_type": "text/plain",
        "extracted_text": "", "ocr_text": "", "version": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_host_local_detection_and_runtime_capability_matrix(monkeypatch) -> None:
    settings = SimpleNamespace(environment="test", host="192.168.1.10", mode="host")
    monkeypatch.setattr(workspace, "get_settings", lambda: settings)
    assert workspace.is_host_local_request(_request("testclient"))
    assert workspace.is_host_local_request(_request("127.0.0.1"))
    assert not workspace.is_host_local_request(_request("not-an-ip"))
    monkeypatch.setattr(workspace.socket, "getaddrinfo", lambda *_a: (_ for _ in ()).throw(OSError("dns")))
    assert workspace.is_host_local_request(_request("192.168.1.10"))
    assert not workspace.is_host_local_request(_request("192.168.1.11"))

    staff = SimpleNamespace(role=UserRole.STAFF)
    admin = SimpleNamespace(role=UserRole.ADMIN)
    monkeypatch.setattr(workspace, "request_device", lambda *_a: None)
    host = workspace.runtime_context(_request("127.0.0.1"), staff, Db())
    assert host.node_mode == "host" and "fleet.manage" not in host.capabilities
    settings.mode = "personal"
    personal = workspace.runtime_context(_request("127.0.0.1"), admin, Db())
    assert personal.node_mode == "personal"
    assert "fleet.manage" not in personal.capabilities
    assert "workspace.manage_host_roots" not in personal.capabilities
    settings.mode = "host"

    device = SimpleNamespace(id="device-1", name="协同机", active=True, status="online", allow_user_shares=True)
    monkeypatch.setattr(workspace, "request_device", lambda *_a: device)
    client = workspace.runtime_context(_request("192.168.1.20"), staff, Db())
    assert client.node_mode == "client" and "workspace.local_share" in client.capabilities
    device.allow_user_shares = False
    client = workspace.runtime_context(_request("192.168.1.20"), staff, Db())
    assert "workspace.local_share" not in client.capabilities
    device.allow_user_shares = True
    admin_context = workspace.runtime_context(_request("192.168.1.20"), admin, Db())
    assert "admin.access" in admin_context.capabilities and "ai.manage" in admin_context.capabilities


def test_file_and_root_permission_guard_matrix(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    item = _item()
    root = _root()
    with pytest.raises(ProblemException) as missing:
        workspace.get_file(Db(), "missing", user, None)
    assert missing.value.code == "WORKSPACE_FILE_NOT_FOUND"
    with pytest.raises(ProblemException) as disabled:
        workspace.get_file(Db(objects={(WorkspaceFile, "file-1"): item}), "file-1", user, None)
    assert disabled.value.code == "WORKSPACE_ROOT_DISABLED"
    root.enabled = False
    with pytest.raises(ProblemException):
        workspace.get_file(Db(objects={(WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root}), "file-1", user, None)
    root.enabled = True
    item.in_scope = False
    with pytest.raises(ProblemException) as out_of_scope:
        workspace.get_file(Db(objects={(WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root}), "file-1", user, None)
    assert out_of_scope.value.code == "WORKSPACE_FILE_OUT_OF_SCOPE"
    item.in_scope = True
    monkeypatch.setattr(workspace, "workspace_root_permissions", lambda *_a, **_k: {"browse": False, "manage_root": False})
    with pytest.raises(ProblemException) as denied:
        workspace.get_file(Db(objects={(WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root}), "file-1", user, None)
    assert denied.value.code == "WORKSPACE_ACCESS_DENIED"
    with pytest.raises(ProblemException):
        workspace.require_root_manager(Db(), "missing", user, None)
    with pytest.raises(ProblemException) as manage_denied:
        workspace.require_root_manager(Db(objects={(WorkspaceRoot, "root-1"): root}), "root-1", user, None)
    assert manage_denied.value.code == "WORKSPACE_ROOT_MANAGE_DENIED"
    monkeypatch.setattr(workspace, "workspace_root_permissions", lambda *_a, **_k: {"browse": True, "manage_root": True})
    assert workspace.get_file(Db(objects={(WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root}), "file-1", user, None) == (item, root)
    assert workspace.require_root_manager(Db(objects={(WorkspaceRoot, "root-1"): root}), "root-1", user, None) is root


@pytest.mark.parametrize(
    "device",
    [
        None,
        SimpleNamespace(id="d", active=False, status="online", allow_user_shares=True),
        SimpleNamespace(id="d", active=True, status="revoked", allow_user_shares=True),
        SimpleNamespace(id="d", active=True, status="online", allow_user_shares=False),
    ],
)
def test_local_share_action_rejects_invalid_device_states(monkeypatch, device) -> None:
    monkeypatch.setattr(workspace, "request_device", lambda *_a: device)
    with pytest.raises(ProblemException):
        workspace.create_local_share_action(_request(), SimpleNamespace(id="user-1"), Db())


def test_preview_open_download_and_freeze_guards(monkeypatch, tmp_path: Path) -> None:
    user = SimpleNamespace(id="user-1")
    request = _request()
    root = _root()
    item = _item()
    monkeypatch.setattr(workspace, "current_device_id", lambda *_a: None)

    monkeypatch.setattr(workspace, "get_file", lambda *_a, **_k: (_item(is_directory=True), root))
    with pytest.raises(ProblemException):
        workspace.preview_workspace_file("file", request, user, Db())
    with pytest.raises(ProblemException):
        workspace.create_local_open_link("file", request, user, Db())
    with pytest.raises(ProblemException):
        workspace.download_workspace_file("file", request, user, Db())

    remote = _root("device")
    monkeypatch.setattr(workspace, "get_file", lambda *_a, **_k: (_item(extracted_text="远端正文"), remote))
    response = workspace.preview_workspace_file("file", request, user, Db())
    assert "远端正文" in response.body.decode()
    monkeypatch.setattr(workspace, "get_file", lambda *_a, **_k: (_item(), remote))
    with pytest.raises(ProblemException) as remote_preview:
        workspace.preview_workspace_file("file", request, user, Db())
    assert remote_preview.value.code == "REMOTE_PREVIEW_REQUIRES_TRANSFER"
    with pytest.raises(ProblemException):
        workspace.download_workspace_file("file", request, user, Db())
    with pytest.raises(ProblemException):
        workspace.freeze_file("file", request, '"1"', user, Db())

    monkeypatch.setattr(workspace, "get_file", lambda *_a, **_k: (item, root))
    monkeypatch.setattr(workspace, "is_host_local_request", lambda _request: False)
    with pytest.raises(ProblemException) as host_only:
        workspace.create_local_open_link("file", request, user, Db())
    assert host_only.value.code == "LOCAL_OPEN_HOST_ONLY"
    with pytest.raises(ProblemException):
        workspace.freeze_file("file", request, '"99"', user, Db())

    source = tmp_path / "通知.txt"
    source.write_text("正文", encoding="utf-8")
    monkeypatch.setattr(workspace, "resolve_workspace_path", lambda *_a: source)
    monkeypatch.setattr(workspace, "may_render_inline", lambda _mime: False)
    item.mime_type = "image/svg+xml"
    svg = workspace.preview_workspace_file("file", request, user, Db())
    assert svg.media_type == "image/svg+xml"
    item.mime_type = "application/octet-stream"
    item.extracted_text = ""
    fallback = workspace.preview_workspace_file("file", request, user, Db())
    assert "默认程序" in fallback.body.decode()


def test_open_token_and_link_target_guards(monkeypatch) -> None:
    request = _request()
    db = Db()
    monkeypatch.setattr(workspace, "is_host_local_request", lambda _request: False)
    with pytest.raises(ProblemException):
        workspace.resolve_local_open_token("valid", request, db)
    monkeypatch.setattr(workspace, "is_host_local_request", lambda _request: True)
    for token in ("!", "x" * 129):
        with pytest.raises(ProblemException) as invalid:
            workspace.resolve_local_open_token(token, request, db)
        assert invalid.value.code == "OPEN_TOKEN_INVALID"
    with pytest.raises(ProblemException) as expired:
        workspace.resolve_local_open_token("valid", request, db)
    assert expired.value.code == "OPEN_GRANT_INVALID"
    grant = SimpleNamespace(revoked_at=None, used_at=None, expires_at=workspace.utcnow(), file_id="file-1")
    with pytest.raises(ProblemException) as unavailable:
        workspace.resolve_local_open_token("valid", request, Db(scalar_values=[grant]))
    assert unavailable.value.code == "OPEN_GRANT_EXPIRED"

    user = SimpleNamespace(id="user-1")
    monkeypatch.setattr(workspace, "can_view_task", lambda *_a: False)
    for payload, code in (
        (WorkspaceFileLinkCreate(entity_type="task", entity_id="missing"), "TASK_NOT_FOUND"),
        (WorkspaceFileLinkCreate(entity_type="report", entity_id="missing"), "PERIOD_REPORT_NOT_FOUND"),
        (WorkspaceFileLinkCreate(entity_type="journal", entity_id="missing"), "JOURNAL_NOT_FOUND"),
    ):
        with pytest.raises(ProblemException) as error:
            workspace.validate_link_target(db, payload, user)
        assert error.value.code == code
    objects = {
        (Task, "task"): SimpleNamespace(id="task"),
        (PeriodReport, "report"): SimpleNamespace(id="report"),
        (WorkJournalEntry, "journal"): SimpleNamespace(id="journal"),
    }
    monkeypatch.setattr(workspace, "can_view_task", lambda *_a: True)
    valid_db = Db(objects=objects)
    workspace.validate_link_target(valid_db, WorkspaceFileLinkCreate(entity_type="task", entity_id="task"), user)
    workspace.validate_link_target(valid_db, WorkspaceFileLinkCreate(entity_type="report", entity_id="report"), user)
    workspace.validate_link_target(valid_db, WorkspaceFileLinkCreate(entity_type="journal", entity_id="journal"), user)


def test_unlink_rejects_wrong_file_and_frozen_link(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    item = _item()
    root = _root()
    monkeypatch.setattr(workspace, "current_device_id", lambda *_a: None)
    monkeypatch.setattr(workspace, "get_file", lambda *_a, **_k: (item, root))
    for link in (None, SimpleNamespace(file_id="other", entity_type="task"), SimpleNamespace(file_id="file-1", entity_type="frozen")):
        db = Db(objects={(WorkspaceLink, "link"): link} if link else {})
        with pytest.raises(ProblemException) as error:
            workspace.unlink_workspace_file("file-1", "link", _request(), '"1"', user, db)
        assert error.value.code == "WORKSPACE_LINK_NOT_FOUND"
