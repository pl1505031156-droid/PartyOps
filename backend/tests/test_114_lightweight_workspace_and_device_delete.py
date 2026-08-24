"""1.1.3 综合修复：轻量文件目录、默认程序打开和设备删除。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient


def test_uos_default_app_handler_is_self_healing_and_ca_aware() -> None:
    project_root = Path(__file__).resolve().parents[2]
    installer = (
        project_root / "packaging" / "uos" / "install-desktop-shortcut.sh"
    ).read_text(encoding="utf-8")
    launcher = (
        project_root / "packaging" / "uos" / "desktop-launcher.sh"
    ).read_text(encoding="utf-8")
    opener = (
        project_root / "packaging" / "uos" / "open-local-file.sh"
    ).read_text(encoding="utf-8")
    desktop_entry = (
        project_root / "packaging" / "uos" / "partyops-file.desktop"
    ).read_text(encoding="utf-8")

    assert '"HOME=$USER_HOME"' in installer
    assert '"XDG_RUNTIME_DIR=$USER_RUNTIME_DIR"' in installer
    assert '"DBUS_SESSION_BUS_ADDRESS=$USER_BUS_ADDRESS"' in installer
    assert "metadata::trusted true" in installer
    assert "xdg-mime query default" in installer
    assert "partyops-file.desktop" in installer
    assert "install-desktop-shortcut.sh" in launcher
    assert 'if [[ ! -f "$CONFIG_ROOT/.desktop-shortcut-created" ]]' not in launcher
    assert "--cacert" in opener
    assert "partyops-internal-ca.crt" in opener
    assert "notify-send" in opener
    assert 'LOCAL_HOST="127.0.0.1"' in opener
    assert '"$SCHEME://$LOCAL_HOST:$PORT/api/v1/workspace/open-tokens/$TOKEN"' in opener
    assert "OPEN_GRANT_EXPIRED" in opener
    assert "CERTIFICATE_FAILED" in opener
    assert "report_result \"OPENED\"" in opener
    assert 'Exec=/opt/partyops/open-local-file.sh "%u"' in desktop_entry


def test_workspace_indexes_names_only_and_open_token_is_one_time(
    client: TestClient,
    admin: dict,
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "轻量文件目录"
    root_path.mkdir()
    source = root_path / "普通通知.wps"
    source.write_text("正文秘密关键词-不得进入原始文件搜索", encoding="utf-8")
    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "轻量文件目录回归", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    scanned = client.post(f"/api/v1/workspace/roots/{root['id']}/scan-now")
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["metadata_only"] == 1
    assert scanned.json()["content_indexed"] == 0
    assert scanned.json()["pending_ocr"] == 0
    assert scanned.json()["content_failed"] == 0

    by_name = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "普通通知"},
    )
    assert by_name.status_code == 200, by_name.text
    item = by_name.json()[0]
    assert item["content_status"] == "metadata_only"
    assert item["preview_text"] == ""
    by_body = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "正文秘密关键词"},
    )
    assert by_body.status_code == 200
    assert by_body.json() == []

    issued = client.post(f"/api/v1/workspace/files/{item['id']}/open-local")
    assert issued.status_code == 200, issued.text
    grant_id = issued.json()["grant_id"]
    assert client.get(f"/api/v1/files/open-grants/{grant_id}").json()["status"] == "created"
    uri = urlparse(issued.json()["open_uri"])
    assert uri.scheme == "partyops-file"
    assert uri.netloc == "open"
    token = uri.path.lstrip("/")
    resolved = client.get(f"/api/v1/workspace/open-tokens/{token}")
    assert resolved.status_code == 200, resolved.text
    assert Path(resolved.text) == source.resolve()
    assert resolved.headers["x-partyops-open-grant-id"] == grant_id
    redeemed = client.get(f"/api/v1/files/open-grants/{grant_id}")
    assert redeemed.status_code == 200
    assert redeemed.json()["status"] == "redeemed"
    completed = client.post(
        f"/api/v1/workspace/open-tokens/{token}/complete",
        json={"result_code": "OPENED", "detail": ""},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"
    assert completed.json()["result_code"] == "OPENED"
    assert completed.json()["opened_at"]
    replay = client.get(f"/api/v1/workspace/open-tokens/{token}")
    assert replay.status_code == 410

    preview = client.get(f"/api/v1/workspace/files/{item['id']}/preview")
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]


def test_windows_file_helper_uses_loopback_and_reports_precise_result() -> None:
    helper = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "windows"
        / "windows_file_open.py"
    ).read_text(encoding="utf-8")

    assert 'return f"{scheme}://127.0.0.1:{port}", context' in helper
    assert "OPEN_GRANT_EXPIRED" in helper
    assert '"DEFAULT_APP_FAILED"' in helper
    assert '_completion(base_url, token, context, "OPENED")' in helper


def test_admin_can_delete_managed_device_and_old_token_is_revoked(
    client: TestClient,
    admin: dict,
) -> None:
    device_name = "可删除纳管设备"
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": device_name},
    )
    assert enrollment.status_code == 201, enrollment.text
    enrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": device_name,
            "architecture": "arm64",
            "platform": "uos",
            "kernel": "4.19.0-arm64-desktop",
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    token = enrolled.json()["device_token"]
    device_id = enrolled.json()["device_id"]
    listed = client.get("/api/v1/admin/devices")
    device = next(item for item in listed.json() if item["id"] == device_id)

    removed = client.delete(
        f"/api/v1/admin/devices/{device_id}",
        headers={"If-Match": str(device["version"])},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["history_preserved"] is True
    assert all(
        item["id"] != device_id
        for item in client.get("/api/v1/admin/devices").json()
    )
    heartbeat = client.post(
        "/api/v1/devices/heartbeat",
        headers={"X-PartyOps-Device-Token": token},
        json={"architecture": "arm64", "platform": "uos"},
    )
    assert heartbeat.status_code == 401

    # 删除会释放显示名称，可使用同名设备重新执行安全入网。
    reenrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": device_name},
    )
    assert reenrollment.status_code == 201, reenrollment.text
    reenrolled = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": reenrollment.json()["code"],
            "name": device_name,
            "architecture": "arm64",
            "platform": "uos",
        },
    )
    assert reenrolled.status_code == 201, reenrolled.text
