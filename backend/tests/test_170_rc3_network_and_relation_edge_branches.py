"""协同公布地址与跨对象关联的剩余安全边界。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import networking
from app.enums import LinkType, ObjectType, UserRole
from app.models import ObjectLink
from app.problems import ProblemException
from app.routers import relations
from app.schemas import ObjectLinkCreate


class _Db:
    def __init__(self, *, gets=None, scalar=None):
        self.gets = dict(gets or {})
        self.scalar_queue = list(scalar or [])
        self.added = []

    def get(self, model, key):
        return self.gets.get((model, key), self.gets.get(key))

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        return None


def _code(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "224.0.0.1"])
def test_bind_host_rejects_unreachable_advertised_addresses(host: str) -> None:
    with pytest.raises(RuntimeError):
        networking.validate_bind_host(
            "0.0.0.0", True, advertised_host=host
        )


def test_advertise_dns_public_ip_and_ipv6_service_url() -> None:
    with pytest.raises(RuntimeError, match="仅本机解析"):
        networking.validate_advertise_host("partyops.localhost")
    networking.validate_advertise_host("partyops.office.lan")
    with pytest.raises(RuntimeError, match="公网"):
        networking.validate_advertise_host("8.8.8.8")
    assert networking.service_url("fd00::1", 18765, tls_enabled=True) == "https://[fd00::1]:18765"


def test_relation_rejects_self_missing_and_foreign_links(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    request = SimpleNamespace(client=None)
    monkeypatch.setattr(relations, "describe_object", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(relations, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(relations, "emit_event", lambda *_args, **_kwargs: None)
    payload = ObjectLinkCreate(
        target_type=ObjectType.TASK,
        target_id="task-1",
        link_type=LinkType.RELATES_TO,
    )
    _code(
        "OBJECT_LINK_SELF",
        lambda: relations.create_object_link(
            "task", "task-1", payload, request, None, user, _Db()
        ),
    )

    _code(
        "OBJECT_LINK_NOT_FOUND",
        lambda: relations.delete_object_link(
            "task", "task-1", "missing", request, "1", user, _Db()
        ),
    )
    foreign = SimpleNamespace(
        source_type=ObjectType.TASK,
        source_id="other",
        target_type=ObjectType.PERIOD_REPORT,
        target_id="report-1",
    )
    _code(
        "OBJECT_LINK_NOT_FOUND",
        lambda: relations.delete_object_link(
            "task",
            "task-1",
            "foreign",
            request,
            "1",
            user,
            _Db(gets={(ObjectLink, "foreign"): foreign}),
        ),
    )


def test_relation_without_idempotency_reuses_existing_link(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    request = SimpleNamespace(client=None)
    link = SimpleNamespace(id="link-1")
    payload = ObjectLinkCreate(
        target_type=ObjectType.PERIOD_REPORT,
        target_id="report-1",
        link_type=LinkType.RELATES_TO,
    )
    monkeypatch.setattr(relations, "describe_object", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(
        relations,
        "visible_links",
        lambda *_args: [{"id": link.id, "direction": "outgoing"}],
    )
    monkeypatch.setattr(relations, "write_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(relations, "emit_event", lambda *_args, **_kwargs: None)
    db = _Db(scalar=[link])
    result = relations.create_object_link(
        "task", "task-1", payload, request, None, user, db
    )
    assert result["id"] == link.id
    assert db.added and all(item is not link for item in db.added)
