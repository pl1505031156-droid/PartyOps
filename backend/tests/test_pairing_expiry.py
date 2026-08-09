"""旧式备份配对令牌有效期回归测试。"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import db_runtime
from app.models import ClientPairing, utcnow


def test_backup_pairing_expires_and_is_revoked(client: TestClient, admin: dict) -> None:
    created = client.post(
        "/api/v1/admin/pairings",
        json={"name": "到期策略测试终端"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["expires_at"] == payload["config"]["pairing_expires_at"]

    with db_runtime.session_factory() as db:
        pairing = db.get(ClientPairing, payload["id"])
        assert pairing is not None
        pairing.created_at = utcnow() - timedelta(
            days=get_settings().backup_pairing_ttl_days + 1
        )
        db.commit()

    response = client.get(
        "/api/v1/backups/latest",
        headers={"X-PartyOps-Pairing": payload["token"]},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "PAIRING_EXPIRED"

    with db_runtime.session_factory() as db:
        pairing = db.get(ClientPairing, payload["id"])
        assert pairing is not None
        assert pairing.active is False


def test_fresh_backup_pairing_remains_compatible(client: TestClient, admin: dict) -> None:
    created = client.post(
        "/api/v1/admin/pairings",
        json={"name": "兼容性测试终端"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()

    response = client.get(
        "/api/v1/notifications/paired-summary",
        headers={"X-PartyOps-Pairing": payload["token"]},
    )
    assert response.status_code == 200, response.text
    assert "unread_count" in response.json()

    listed = client.get("/api/v1/admin/pairings")
    assert listed.status_code == 200
    current = next(item for item in listed.json() if item["id"] == payload["id"])
    assert current["expires_at"] == payload["expires_at"]
