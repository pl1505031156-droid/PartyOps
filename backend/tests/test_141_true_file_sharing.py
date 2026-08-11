"""1.4.2 真文件共享、跨机阅读与 0017 迁移契约。"""

from __future__ import annotations

from datetime import timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app import client_agent, local_ai
from app.config import get_settings
from app.database import Base, db_runtime
from app.models import Transfer, utcnow
from app.routers.fleet import safe_archive_component, safe_name


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def _enroll_windows_device(client: TestClient, name: str) -> dict:
    enrollment = client.post("/api/v1/admin/devices/enrollments", json={"name": name})
    assert enrollment.status_code == 201, enrollment.text
    response = client.post(
        "/api/v1/devices/enroll",
        json={
            "code": enrollment.json()["code"],
            "name": name,
            "architecture": "amd64",
            "platform": "windows",
            "kernel": "Windows 11",
            "app_version": "1.4.2",
            "agent_version": "1.4.2",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_host_browser_download_has_truthful_direction_and_progress(
    client: TestClient,
    admin: dict,
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "主机下载语义"
    root_path.mkdir()
    source = root_path / "主机材料.txt"
    source.write_text("1.4.2 主机浏览器下载", encoding="utf-8")
    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "主机下载语义", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    scanned = client.post(f"/api/v1/workspace/roots/{root['id']}/scan-now")
    assert scanned.status_code == 200, scanned.text
    item = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": source.name},
    ).json()[0]

    created_download = client.post(
        "/api/v1/workspace/downloads",
        json={"item_ids": [item["id"]], "bundle_mode": "single", "delivery": "browser"},
    )
    assert created_download.status_code == 201, created_download.text
    transfer = next(
        item
        for item in client.get("/api/v1/transfers").json()
        if item["id"] == created_download.json()["transfer_id"]
    )
    assert transfer["direction"] == "host_to_device"
    assert transfer["delivery_mode"] == "browser_direct"
    assert transfer["status"] == "completed"
    assert transfer["completed_chunks"] == transfer["total_chunks"]


def test_completed_transfer_download_supports_chinese_filename(
    client: TestClient,
    admin: dict,
) -> None:
    body = "协同电脑中文文件名内容".encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    with db_runtime.session_factory() as db:
        transfer = Transfer(
            direction="device_to_host",
            status="completed",
            original_name="协同机共享材料.txt",
            relative_path="协同机共享材料.txt",
            size_bytes=len(body),
            sha256=digest,
            chunk_size=8 * 1024 * 1024,
            total_chunks=1,
            completed_chunks=1,
            requested_by=admin["id"],
            expires_at=utcnow() + timedelta(days=1),
            delivery_mode="browser",
            bundle_mode="single",
            result_name="协同机共享材料.txt",
            result_sha256=digest,
        )
        db.add(transfer)
        db.commit()
        db.refresh(transfer)
        transfer_id = transfer.id
    target = get_settings().inbox_dir / f"{transfer_id}-{safe_name('协同机共享材料.txt')}"
    target.write_bytes(body)

    response = client.get(f"/api/v1/transfers/{transfer_id}/content")
    assert response.status_code == 200, response.text
    assert response.content == body
    assert "filename*=utf-8''" in response.headers["content-disposition"].lower()


def test_staff_publishes_team_root_and_creates_both_download_channels(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    notification_body = b"notice-141!!"
    notification_digest = hashlib.sha256(notification_body).hexdigest()
    _login(client, "admin")
    colleague_response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "share_colleague",
            "display_name": "共享同事",
            "password": "PartyOps@2026",
            "role": "staff",
        },
    )
    assert colleague_response.status_code in {201, 409}, colleague_response.text
    colleague = (
        colleague_response.json()
        if colleague_response.status_code == 201
        else next(
            item
            for item in client.get("/api/v1/admin/users").json()
            if item["username"] == "share_colleague"
        )
    )
    device = _enroll_windows_device(client, "1.4.2 文件共享协同机")
    device_headers = {"X-PartyOps-Device-Token": device["device_token"]}

    _login(client, "staff")
    browser_token = client.post("/api/v1/devices/browser-token", headers=device_headers)
    assert browser_token.status_code == 200, browser_token.text
    # URL 中的短期启动票据不能被直接当成长期业务 Cookie 使用。
    client.cookies.set("partyops_device_context", browser_token.json()["token"])
    direct_use = client.post("/api/v1/workspace/local-share-actions")
    assert direct_use.status_code == 409
    client.cookies.delete("partyops_device_context")
    launch = client.get(
        "/device-launch",
        params={"token": browser_token.json()["token"]},
        follow_redirects=False,
    )
    assert launch.status_code == 303
    context = client.get("/api/v1/runtime/context")
    assert context.status_code == 200, context.text
    assert context.json()["node_mode"] == "client"
    assert context.json()["device_id"] == device["device_id"]
    assert "workspace.local_share" in context.json()["capabilities"]

    action = client.post("/api/v1/workspace/local-share-actions")
    assert action.status_code == 201, action.text
    parsed = urlparse(action.json()["open_uri"])
    assert parsed.scheme == "partyops-client" and parsed.netloc == "manage-shares"
    action_token = parsed.path.strip("/")
    created = client.post(
        "/api/v1/devices/workspace/roots",
        headers=device_headers,
        json={
            "name": "普通用户发布资料",
            "remote_key": "staff_shared_141",
            "action_token": action_token,
        },
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert root["approval_status"] == "approved"
    assert root["enabled"] is True
    replay = client.post(
        "/api/v1/devices/workspace/roots",
        headers=device_headers,
        json={
            "name": "重复消费",
            "remote_key": "staff_shared_replay",
            "action_token": action_token,
        },
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "LOCAL_SHARE_ACTION_INVALID"

    indexed = client.post(
        "/api/v1/devices/workspace/index-delta",
        headers=device_headers,
        json={
            "root_id": root["id"],
            "files": [
                {
                    "relative_path": "材料",
                    "name": "材料",
                    "is_directory": True,
                    "size_bytes": 0,
                },
                {
                    "relative_path": "材料/通知.txt",
                    "name": "通知.txt",
                    "parent_relative_path": "材料",
                    "size_bytes": len(notification_body),
                    "sha256": notification_digest,
                },
                {
                    "relative_path": "材料/清单.txt",
                    "name": "清单.txt",
                    "parent_relative_path": "材料",
                    "size_bytes": 18,
                    "sha256": "b" * 64,
                },
                {
                    "relative_path": "超限文件.bin",
                    "name": "超限文件.bin",
                    "size_bytes": 20 * 1024**3 + 1,
                    "sha256": "c" * 64,
                },
            ],
        },
    )
    assert indexed.status_code == 200, indexed.text
    refreshed_root = next(
        item for item in client.get("/api/v1/workspace/roots").json()
        if item["id"] == root["id"]
    )
    assert refreshed_root["file_count"] == 3
    assert refreshed_root["directory_count"] == 1
    all_items = client.get(
        "/api/v1/workspace/search", params={"root_id": root["id"], "keyword": "材料"}
    ).json()
    file_item = next(item for item in all_items if item["name"] == "通知.txt")
    second_file = next(item for item in all_items if item["name"] == "清单.txt")
    folder_item = next(item for item in all_items if item["name"] == "材料")
    assert file_item["permissions"]["download"] is True
    assert file_item["permissions"]["manage_root"] is True

    too_large_item = next(
        item
        for item in client.get(
            "/api/v1/workspace/files", params={"root_id": root["id"]}
        ).json()
        if item["name"] == "超限文件.bin"
    )
    too_large = client.post(
        "/api/v1/workspace/downloads",
        json={"item_ids": [too_large_item["id"]], "bundle_mode": "single", "delivery": "browser"},
    )
    assert too_large.status_code == 413

    browser_download = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [file_item["id"]],
            "bundle_mode": "single",
            "delivery": "browser",
        },
    )
    assert browser_download.status_code == 201, browser_download.text
    assert browser_download.json()["content_url"].endswith("/content")
    browser_transfer_id = browser_download.json()["transfer_id"]
    uploaded = client.put(
        f"/api/v1/devices/transfers/{browser_transfer_id}/chunks/0",
        headers={**device_headers, "X-Chunk-SHA256": notification_digest},
        content=notification_body,
    )
    assert uploaded.status_code == 200, uploaded.text
    # 末块只完成中转与整体哈希；Agent 在确认源文件传输期间未变化后，
    # 必须显式 finalize，避免主机过早交付不稳定快照。
    assert uploaded.json()["status"] == "transferring"
    finalized = client.post(
        f"/api/v1/devices/transfers/{browser_transfer_id}/finalize",
        headers=device_headers,
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"
    downloaded = client.get(
        f"/api/v1/transfers/{browser_transfer_id}/content",
        params={"inline": True},
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == notification_body
    current_device_download = client.post(
        "/api/v1/workspace/downloads",
        json={
            "item_ids": [file_item["id"]],
            "bundle_mode": "single",
            "delivery": "current_device",
        },
    )
    assert current_device_download.status_code == 201, current_device_download.text
    own_transfer = next(
        item
        for item in client.get("/api/v1/transfers").json()
        if item["id"] == current_device_download.json()["transfer_id"]
    )
    assert own_transfer["direction"] == "device_to_device"
    assert own_transfer["delivery_mode"] == "current_device"

    for item_ids, bundle_mode in (
        ([file_item["id"], second_file["id"]], "selection_zip"),
        ([folder_item["id"]], "folder_zip"),
    ):
        bundled = client.post(
            "/api/v1/workspace/downloads",
            json={"item_ids": item_ids, "bundle_mode": bundle_mode, "delivery": "browser"},
        )
        assert bundled.status_code == 201, bundled.text
        transfer = next(
            item for item in client.get("/api/v1/transfers").json()
            if item["id"] == bundled.json()["transfer_id"]
        )
        assert transfer["bundle_mode"] == bundle_mode

    # 即使文件已经进入主机受管接收箱，最终阅读/下载仍必须复核源目录的当前权限。
    # 这能阻止用户在目录停用或成员授权撤销后继续读取旧的中转副本。
    completed_body = "授权撤销后不可继续阅读".encode("utf-8")
    completed_digest = hashlib.sha256(completed_body).hexdigest()
    with db_runtime.session_factory() as db:
        completed_transfer = Transfer(
            direction="device_to_host",
            status="completed",
            source_device_id=device["device_id"],
            source_file_id=file_item["id"],
            item_ids=[file_item["id"]],
            original_name="通知.txt",
            relative_path="材料/通知.txt",
            size_bytes=len(completed_body),
            sha256=completed_digest,
            chunk_size=8 * 1024 * 1024,
            total_chunks=1,
            completed_chunks=1,
            requested_by=colleague["id"],
            expires_at=utcnow() + timedelta(days=1),
            delivery_mode="browser",
            bundle_mode="single",
            result_name="通知.txt",
            result_sha256=completed_digest,
        )
        db.add(completed_transfer)
        db.commit()
        db.refresh(completed_transfer)
        completed_transfer_id = completed_transfer.id
    completed_target = (
        get_settings().inbox_dir
        / f"{completed_transfer_id}-{safe_name('通知.txt')}"
    )
    completed_target.write_bytes(completed_body)

    # 团队共享默认让另一名普通用户浏览下载；切到指定成员后立即隐藏。
    _login(client, "share_colleague")
    assert any(item["id"] == root["id"] for item in client.get("/api/v1/workspace/roots").json())
    started_before_revoke = client.post(
        "/api/v1/workspace/downloads",
        json={"item_ids": [file_item["id"]], "bundle_mode": "single", "delivery": "browser"},
    )
    assert started_before_revoke.status_code == 201, started_before_revoke.text
    _login(client, "staff")
    current_root = next(item for item in client.get("/api/v1/workspace/roots").json() if item["id"] == root["id"])
    selected = client.patch(
        f"/api/v1/workspace/roots/{root['id']}/sharing",
        headers={"If-Match": str(current_root["version"])},
        json={"share_scope": "selected", "semantic_content_enabled": False},
    )
    assert selected.status_code == 200, selected.text
    members = client.put(
        f"/api/v1/workspace/roots/{root['id']}/members",
        headers={"If-Match": str(selected.json()["version"])},
        json={"members": []},
    )
    assert members.status_code == 200, members.text
    _login(client, "share_colleague")
    assert not any(item["id"] == root["id"] for item in client.get("/api/v1/workspace/roots").json())
    revoked_content = client.get(
        f"/api/v1/transfers/{completed_transfer_id}/content",
        params={"inline": True},
    )
    assert revoked_content.status_code == 403
    assert revoked_content.json()["code"] == "GRANT_DENIED"
    stopped = client.put(
        f"/api/v1/devices/transfers/{started_before_revoke.json()['transfer_id']}/chunks/0",
        headers={**device_headers, "X-Chunk-SHA256": "0" * 64},
        content=b"x" * 12,
    )
    assert stopped.status_code == 403
    assert stopped.json()["code"] == "GRANT_DENIED"
    client.cookies.delete("partyops_device_context")
    _login(client, "admin")


def test_client_share_rejects_nested_roots_and_archive_names_are_flat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "共享"
    child = root / "下级"
    child.mkdir(parents=True)
    config_path = tmp_path / "client.json"
    config: dict[str, object] = {"shared_roots": []}
    monkeypatch.setattr(
        client_agent,
        "register_shared_root",
        lambda *_args, **_kwargs: {
            "id": "root-1",
            "approval_status": "approved",
            "enabled": True,
        },
    )
    client_agent.add_shared_root("http://host", "token", config, config_path, root)
    with pytest.raises(ValueError, match="重复或相互嵌套"):
        client_agent.add_shared_root("http://host", "token", config, config_path, child)
    component = safe_archive_component("../../异常\\目录:名称")
    assert "/" not in component and "\\" not in component and component not in {".", ".."}


def test_bge_manifest_uses_cls_pooling_and_query_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    np = pytest.importorskip("numpy", reason="BGE ONNX 运行时未安装")
    onnxruntime = pytest.importorskip("onnxruntime", reason="BGE ONNX 运行时未安装")
    tokenizers = pytest.importorskip("tokenizers", reason="BGE 分词运行时未安装")
    captured: list[str] = []

    class Encoded:
        ids = [101, 102]
        attention_mask = [1, 1]
        type_ids = [0, 0]

    class FakeTokenizer:
        def encode_batch(self, texts: list[str]) -> list[Encoded]:
            captured.extend(texts)
            return [Encoded() for _item in texts]

    class Input:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_inputs(self) -> list[Input]:
            return [Input("input_ids"), Input("attention_mask"), Input("token_type_ids")]

        def run(self, *_args, **_kwargs):
            return [np.asarray([[[3.0, 4.0], [100.0, 100.0]]], dtype=np.float32)]

    model_file = tmp_path / "model.onnx"
    model_file.write_bytes(b"test")
    monkeypatch.setattr(local_ai, "_component_file", lambda *_args: model_file)
    monkeypatch.setattr(onnxruntime, "InferenceSession", FakeSession)
    monkeypatch.setattr(tokenizers.Tokenizer, "from_file", lambda *_args: FakeTokenizer())
    pack = SimpleNamespace(
        id="embedding-pack",
        manifest={
            "components": {
                "embedding": {
                    "model_file": "model.onnx",
                    "tokenizer_file": "tokenizer.json",
                    "pooling": "cls",
                    "query_prefix": "为这个句子生成表示：",
                    "dimension": 2,
                }
            }
        },
    )
    vector = np.frombuffer(
        local_ai.EmbeddingRuntime().encode(pack, ["党建资料"], is_query=True)[0],
        dtype=np.float32,
    )
    assert captured == ["为这个句子生成表示：党建资料"]
    assert vector == pytest.approx(np.asarray([0.6, 0.8], dtype=np.float32))


def test_0017_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "upgrade-0017.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        config.attributes["connection"] = connection
        command.stamp(config, "0017")
        command.downgrade(config, "0016")
        command.upgrade(config, "0017")

    inspector = inspect(engine)
    assert "allow_user_shares" in {item["name"] for item in inspector.get_columns("devices")}
    assert {
        "published_by_user_id",
        "share_scope",
        "semantic_content_enabled",
        "published_at",
    }.issubset({item["name"] for item in inspector.get_columns("workspace_roots")})
    assert {"workspace_root_members", "local_share_actions", "ai_model_activations"}.issubset(
        set(inspector.get_table_names())
    )
    assert {"delivery_mode", "bundle_mode", "item_ids", "result_name", "result_sha256"}.issubset(
        {item["name"] for item in inspector.get_columns("transfers")}
    )
