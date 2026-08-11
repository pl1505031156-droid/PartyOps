"""局域网绑定、共享授权、证书与活动时间线的发布安全回归。"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import networking, pki, workspace_access
from app.enums import ObjectType, UserRole, WorkspaceRootSource
from app.models import ActivityEvent, Device, ObjectLink, User, WorkspaceRoot, WorkspaceRootMember
from app.problems import ProblemException
from app.routers import guidance, relations


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


def test_network_discovery_and_binding_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        networking.socket,
        "getaddrinfo",
        lambda *_args: (_ for _ in ()).throw(OSError("dns")),
    )

    class _Probe:
        def connect(self, _address):
            raise OSError("offline")

        def close(self):
            return None

    monkeypatch.setattr(networking.socket, "socket", lambda *_args: _Probe())
    assert networking.discover_lan_addresses() == []

    networking.validate_bind_host("8.8.8.8", production=False)
    with pytest.raises(RuntimeError, match="明确"):
        networking.validate_bind_host("0.0.0.0", production=True)
    with pytest.raises(RuntimeError, match="公网"):
        networking.validate_bind_host("8.8.8.8", production=True)
    networking.validate_bind_host("partyops.local", production=True)

    networking.validate_transport_security(host="192.168.1.2", production=True, tls_enabled=True)
    networking.validate_transport_security(host="127.0.0.1", production=True, tls_enabled=False)
    networking.validate_transport_security(host="127.0.0.2", production=True, tls_enabled=False)
    networking.validate_transport_security(host="localhost", production=True, tls_enabled=False)
    with pytest.raises(RuntimeError, match="HTTPS"):
        networking.validate_transport_security(host="partyops.local", production=True, tls_enabled=False)
    assert networking.service_url("0.0.0.0", 18765) == "http://127.0.0.1:18765"


def test_enrollment_url_requires_reachable_trusted_lan_address() -> None:
    base = dict(
        configured_host="0.0.0.0",
        configured_port=18765,
        request_base_url="http://192.168.8.20:18765/",
        lan_candidates=["192.168.8.20"],
        tls_enabled=True,
    )
    assert networking.enrollment_service_url(requested_host=None, **base) == "https://192.168.8.20:18765"
    named = {
        **base,
        "configured_host": "partyops.local",
        "request_base_url": "http://127.0.0.1/",
    }
    assert networking.enrollment_service_url(requested_host=None, **named) == "https://partyops.local:18765"
    single = {
        **base,
        "request_base_url": "http://127.0.0.1/",
    }
    assert networking.enrollment_service_url(requested_host=None, **single) == "https://192.168.8.20:18765"
    with pytest.raises(ValueError, match="可信"):
        networking.enrollment_service_url(requested_host="192.168.8.99", **base)
    ambiguous = {**base, "request_base_url": "http://127.0.0.1", "lan_candidates": ["192.168.8.20", "192.168.8.21"]}
    with pytest.raises(LookupError, match="明确选择"):
        networking.enrollment_service_url(requested_host=None, **ambiguous)


class _AccessDb:
    def __init__(self, objects=None, member=None, grants=None):
        self.objects = objects or {}
        self.member = member
        self.grants = grants or []
        self.scalar_calls = 0

    def get(self, model, object_id):
        return self.objects.get((model, object_id))

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.member

    def scalars(self, _statement):
        return _Rows(self.grants)


def _device_root():
    device = SimpleNamespace(id="device-1", active=True, status="online", allow_host_access=True)
    root = SimpleNamespace(
        id="root-1",
        enabled=True,
        source=WorkspaceRootSource.DEVICE,
        device_id="device-1",
        approval_status="approved",
        published_by_user_id="publisher",
        share_scope="selected",
    )
    return device, root


def test_workspace_access_device_root_member_grant_and_host_gate() -> None:
    staff = SimpleNamespace(id="staff", role=UserRole.STAFF)
    admin = SimpleNamespace(id="admin", role=UserRole.ADMIN)
    device, root = _device_root()
    db = _AccessDb({(Device, "device-1"): device, (WorkspaceRoot, "root-1"): root})
    assert not workspace_access.grant_allows(db, staff, "device-1", "root-1", "execute")

    device.status = "quarantined"
    assert not workspace_access.grant_allows(db, staff, "device-1", "root-1", "download")
    device.status = "online"
    assert workspace_access.grant_allows(db, admin, "device-1", "root-1", "download")

    publisher = SimpleNamespace(id="publisher", role=UserRole.STAFF)
    assert workspace_access.grant_allows(db, publisher, "device-1", "root-1", "share")
    root.share_scope = "team"
    assert workspace_access.grant_allows(db, staff, "device-1", "root-1", "download")
    root.share_scope = "selected"
    db.member = SimpleNamespace(can_download=False, can_send=True)
    assert workspace_access.grant_allows(db, staff, "device-1", "root-1", "share")
    db.member = None
    db.grants = [SimpleNamespace(capabilities=["*"])]
    assert workspace_access.grant_allows(db, staff, "device-1", "root-1", "upload")

    permissions = workspace_access.workspace_root_permissions(db, root, publisher, "device-1")
    assert permissions["manage_root"] is True
    root.approval_status = "pending"
    assert not workspace_access.workspace_root_permissions(db, root, staff)["browse"]

    host = SimpleNamespace(id="host-root", enabled=True, source=WorkspaceRootSource.HOST)
    device.allow_host_access = False
    assert not workspace_access.workspace_root_permissions(db, host, staff, "device-1")["browse"]
    assert workspace_access.workspace_root_permissions(db, host, admin, "device-1")["manage_root"]


def test_guidance_network_readiness_matrix(monkeypatch) -> None:
    for host, tls, expected in (
        ("127.0.0.1", True, False),
        ("169.254.1.1", True, False),
        ("8.8.8.8", True, False),
        ("partyops.local", False, False),
        ("192.168.8.20", True, True),
    ):
        monkeypatch.setattr(
            guidance,
            "get_settings",
            lambda host=host, tls=tls: SimpleNamespace(host=host, tls_enabled=tls),
        )
        assert guidance.host_network_ready() is expected
    step = guidance.enablement_step("files", "文件", "说明", "/workspace", "打开", True)
    assert step.complete and step.route == "/workspace"


def test_pki_generates_dns_certificate_and_device_certificate(monkeypatch, tmp_path) -> None:
    secrets_dir = tmp_path / "secrets"
    settings = SimpleNamespace(
        secrets_dir=secrets_dir,
        host="partyops.local",
        tls_cert_file=None,
        tls_key_file=None,
        tls_client_ca_file=None,
    )
    monkeypatch.setattr(
        pki.socket,
        "gethostbyname_ex",
        lambda *_args: (_ for _ in ()).throw(OSError("no dns")),
    )
    material = pki.ensure_tls_material(settings)
    assert material["ca_path"].is_file() and settings.tls_cert_file.is_file()
    server = x509.load_pem_x509_certificate(settings.tls_cert_file.read_bytes())
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert x509.DNSName("partyops.local") in san

    compatibility = pki.issue_device_certificate(settings, "device-1", None)
    assert compatibility["certificate_pem"] == "" and "BEGIN CERTIFICATE" in compatibility["ca_certificate_pem"]

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "device-1")]))
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode("utf-8")
    )
    issued = pki.issue_device_certificate(settings, "device-1", csr)
    assert "BEGIN CERTIFICATE" in issued["certificate_pem"]
    assert len(issued["certificate_fingerprint"]) == 64


class _RelationDb:
    def __init__(self, link=None, rows=None, actors=None):
        self.link = link
        self.rows = rows or []
        self.actors = actors or []
        self.deleted = []
        self.commits = 0
        self.scalar_batches = 0

    def get(self, model, _identity):
        return self.link if model is ObjectLink else None

    def scalars(self, _statement):
        self.scalar_batches += 1
        return _Rows(self.rows if self.scalar_batches == 1 else self.actors)

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commits += 1


def test_relation_delete_guards_and_activity_actor_projection(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    monkeypatch.setattr(relations, "describe_object", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(relations, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(relations, "emit_event", lambda *_args, **_kwargs: None)

    with pytest.raises(ProblemException) as missing:
        relations.delete_object_link("task", "task-1", "missing", request, "1", user, _RelationDb())
    assert missing.value.code == "OBJECT_LINK_NOT_FOUND"
    link = SimpleNamespace(
        id="link-1",
        source_type=ObjectType.TASK,
        source_id="task-1",
        target_type=ObjectType.PERIOD_REPORT,
        target_id="report-1",
        version=2,
        created_by="other",
    )
    with pytest.raises(ProblemException) as conflict:
        relations.delete_object_link("task", "task-1", link.id, request, "1", user, _RelationDb(link))
    assert conflict.value.code == "VERSION_CONFLICT"
    with pytest.raises(ProblemException) as denied:
        relations.delete_object_link("task", "task-1", link.id, request, "2", user, _RelationDb(link))
    assert denied.value.code == "OBJECT_LINK_DENIED"
    link.created_by = user.id
    db = _RelationDb(link)
    assert relations.delete_object_link("task", "task-1", link.id, request, "2", user, db) == {"deleted": True}
    assert db.deleted == [link] and db.commits == 1

    now = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id="event-1", object_type=ObjectType.TASK, object_id="task-1",
            event_code="object.linked", actor_id="actor-1", happened_at=now,
            recorded_at=now, event_data={}, correlation_id="trace-1",
        ),
        SimpleNamespace(
            id="event-2", object_type=ObjectType.TASK, object_id="task-1",
            event_code="unknown", actor_id=None, happened_at=now,
            recorded_at=now, event_data={}, correlation_id="trace-2",
        ),
    ]
    actor = SimpleNamespace(id="actor-1", display_name="办理人", role=UserRole.ADMIN)
    activity = relations.get_object_activity("task", "task-1", 100, user, _RelationDb(rows=rows, actors=[actor]))
    assert activity[0]["actor_name"] == "办理人"
    assert activity[1]["actor_name"] == "系统" and activity[1]["event_label"] == "更新业务记录"
