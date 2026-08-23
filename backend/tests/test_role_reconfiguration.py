"""系统设置重新配置运行角色的本机授权与一次性标记。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.routers import admin as admin_router


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_admin_can_prepare_local_role_reconfiguration(
    client: TestClient,
    admin: dict,
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "reconfigure-request.json"
    monkeypatch.setattr(admin_router, "_role_reconfigure_marker_path", lambda: marker)

    response = client.post("/api/v1/system/reconfigure-request")

    assert response.status_code == 200, response.text
    assert response.json()["deep_link"] == "partyops-client://reconfigure"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["requested_by"] == admin["id"]
    assert payload["expires_at"] - payload["requested_at"] == 120


def test_staff_cannot_change_machine_role(
    client: TestClient,
    admin: dict,
    staff: dict,
    monkeypatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "reconfigure-request.json"
    monkeypatch.setattr(admin_router, "_role_reconfigure_marker_path", lambda: marker)
    _login(client, "staff")

    response = client.post("/api/v1/system/reconfigure-request")

    assert response.status_code == 403
    assert not marker.exists()
    _login(client, "admin")


def test_remote_page_cannot_write_host_reconfiguration_marker(
    client: TestClient,
    admin: dict,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """远端协同页面可以唤起自己的本地深链，但不能写主机的启动标记。"""

    marker = tmp_path / "reconfigure-request.json"
    monkeypatch.setattr(admin_router, "_role_reconfigure_marker_path", lambda: marker)
    monkeypatch.setattr(admin_router, "_request_from_host_desktop", lambda _request: False)

    response = client.post("/api/v1/system/reconfigure-request")

    assert response.status_code == 403
    assert response.json()["code"] == "ROLE_RECONFIGURE_LOCAL_REQUIRED"
    assert not marker.exists()


def test_reconfiguration_marker_write_failure_is_explicit_and_keeps_old_file(
    client: TestClient,
    admin: dict,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """配置目录不可写时不得伪造成功，也不得破坏已有角色请求。"""

    marker = tmp_path / "reconfigure-request.json"
    marker.write_text('{"format_version":1,"requested_at":1}', encoding="utf-8")
    monkeypatch.setattr(admin_router, "_role_reconfigure_marker_path", lambda: marker)
    monkeypatch.setattr(Path, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("拒绝访问")))

    response = client.post("/api/v1/system/reconfigure-request")

    assert response.status_code == 500
    assert response.json()["code"] == "ROLE_RECONFIGURE_MARKER_FAILED"
    assert json.loads(marker.read_text(encoding="utf-8"))["requested_at"] == 1
