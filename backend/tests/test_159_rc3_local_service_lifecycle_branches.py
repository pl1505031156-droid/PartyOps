"""本机公文排版服务票据、会话和清理生命周期分支。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import official_format_service as service
from app.official_format import LocalDocument, OfficialFormatError


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.test",
        "http://",
        "http://user@example.test",
        "http://example.test/path",
        "http://example.test?query=1",
        "http://example.test#fragment",
        "http://example.test\r\nInjected: x",
        "http://" + "a" * 520,
    ],
)
def test_origin_rejects_credentials_paths_and_injection(origin: str) -> None:
    with pytest.raises(ValueError, match="页面来源无效"):
        service.normalize_origin(origin)
    assert service.normalize_origin("HTTPS://Example.Test/") == "https://example.test"


def test_ticket_secret_signature_expiry_origin_and_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="密钥未就绪"):
        service.issue_local_format_ticket(
            "short", origin="http://example.test", user_id="u", device_id="d"
        )
    now = datetime(2026, 8, 25, tzinfo=UTC)
    ticket, expires = service.issue_local_format_ticket(
        "s" * 32, origin="http://example.test", user_id="u", device_id="d", now=now
    )
    assert expires > now
    claims = service.verify_local_format_ticket(
        "s" * 32, ticket, origin="http://example.test", now=now
    )
    assert claims["user_id"] == "u" and claims["device_id"] == "d"
    for changed, origin, current in (
        (ticket + "x", "http://example.test", now),
        (ticket, "http://other.test", now),
        (ticket, "http://example.test", expires),
        ("bad-ticket", "http://example.test", now),
    ):
        with pytest.raises(OfficialFormatError) as raised:
            service.verify_local_format_ticket(
                "s" * 32, changed, origin=origin, now=current
            )
        assert raised.value.code == "LOCAL_TICKET_INVALID"

    payload = {
        "purpose": "wrong",
        "expires": int((now + timedelta(minutes=1)).timestamp()),
        "origin": "http://example.test",
        "nonce": "x" * 24,
    }
    encoded = service._b64encode(service.json.dumps(payload).encode())
    signature = service._b64encode(
        service.hmac.new(b"s" * 32, encoded.encode(), service.hashlib.sha256).digest()
    )
    with pytest.raises(OfficialFormatError):
        service.verify_local_format_ticket(
            "s" * 32, f"{encoded}.{signature}", origin="http://example.test", now=now
        )
    payload["purpose"] = "official-format"
    payload["nonce"] = "short"
    encoded = service._b64encode(service.json.dumps(payload).encode())
    signature = service._b64encode(
        service.hmac.new(b"s" * 32, encoded.encode(), service.hashlib.sha256).digest()
    )
    with pytest.raises(OfficialFormatError):
        service.verify_local_format_ticket(
            "s" * 32, f"{encoded}.{signature}", origin="http://example.test", now=now
        )


def test_service_session_document_cleanup_and_close(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(OfficialFormatError, match="设备凭据不完整"):
        service.OfficialFormatLocalService(secret="short", config_dir=tmp_path)
    formatter = service.OfficialFormatLocalService(
        secret="s" * 32, config_dir=tmp_path, idle_timeout=1
    )
    assert formatter.idle_timeout == 30
    session = formatter.create_session("http://example.test")
    token = formatter.session_token(session)
    assert token and session.plain_token == ""
    with pytest.raises(RuntimeError, match="已交付"):
        formatter.session_token(session)

    formatter.remove_document(session, "missing")
    inside = session.workspace / "source.docx"
    output = session.workspace / "output.docx"
    inside.write_bytes(b"docx")
    output.write_bytes(b"docx")
    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"keep")
    session.documents["inside"] = LocalDocument(
        source=inside, original_stem="正文", converted=False, output=output
    )
    session.documents["outside"] = LocalDocument(
        source=outside, original_stem="外部", converted=False
    )
    formatter.remove_document(session, "inside")
    assert not inside.exists() and not output.exists()
    formatter.remove_document(session, "outside")
    assert outside.exists()
    formatter.remove_session("missing")
    formatter.remove_session(session.id)
    assert not session.workspace.exists()

    calls = []
    formatter.server = SimpleNamespace(
        shutdown=lambda: calls.append("shutdown"),
        server_close=lambda: calls.append("close"),
    )
    formatter.thread = SimpleNamespace(
        join=lambda timeout: calls.append(("thread", timeout))
    )
    formatter.cleanup_thread = SimpleNamespace(
        join=lambda timeout: calls.append(("cleanup", timeout))
    )
    monkeypatch.setattr(
        service, "_append_stage_log", lambda *_args: calls.append("logged")
    )
    formatter.close()
    assert calls == ["shutdown", "close", ("thread", 2), ("cleanup", 2), "logged"]


def test_cleanup_loop_expires_sessions_and_nonces(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    formatter = service.OfficialFormatLocalService(
        secret="s" * 32, config_dir=tmp_path, idle_timeout=30
    )
    old = formatter.create_session("http://example.test")
    old.last_activity = 10.0
    fresh = formatter.create_session("http://example.test")
    fresh.last_activity = 95.0
    formatter.used_nonces = {"old": -100.0, "fresh": 95.0}
    waits = iter([False, True])
    monkeypatch.setattr(formatter.stop_event, "wait", lambda _seconds: next(waits))
    monkeypatch.setattr(service.time, "monotonic", lambda: 100.0)
    removed = []
    original = formatter.remove_session

    def remove(session_id: str):
        removed.append(session_id)
        original(session_id)

    monkeypatch.setattr(formatter, "remove_session", remove)
    formatter._cleanup_loop()
    assert old.id in removed and fresh.id not in removed
    assert formatter.used_nonces == {"fresh": 95.0}
