"""1.3.0 东方主题、本地智能降级与模型包边界测试。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.appearance import automatic_season
from app.config import get_settings
from app.database import Base, db_runtime
from app.enums import RecommendationStatus, SeasonTheme
from app.models import AIModelPack, AIRecommendation, BackgroundJob
from app.model_packs import model_pack_root
from app import models  # noqa: F401
from .conftest import create_task


def test_season_boundaries_do_not_depend_on_system_tzdata() -> None:
    assert automatic_season(datetime(2026, 2, 3)) == SeasonTheme.WINTER
    assert automatic_season(datetime(2026, 2, 4)) == SeasonTheme.SPRING
    assert automatic_season(datetime(2026, 5, 5, tzinfo=timezone.utc)) == SeasonTheme.SUMMER
    assert automatic_season(datetime(2026, 8, 7)) == SeasonTheme.AUTUMN
    assert automatic_season(datetime(2026, 11, 7)) == SeasonTheme.WINTER


def test_appearance_preferences_are_versioned_and_effective(
    client: TestClient, admin: dict
) -> None:
    current = client.get("/api/v1/me/appearance")
    assert current.status_code == 200, current.text
    updated = client.patch(
        "/api/v1/me/appearance",
        headers={"If-Match": str(current.json()["version"])},
        json={
            "art_level": "reduced",
            "reduce_motion": True,
            "theme_override": "winter",
        },
    )
    assert updated.status_code == 200, updated.text
    context = client.get("/api/v1/appearance/context")
    assert context.status_code == 200, context.text
    assert context.json() == {
        "effective_season": "winter",
        "art_level": "reduced",
        "reduce_motion": True,
        "theme_mode": context.json()["theme_mode"],
    }

    conflict = client.patch(
        "/api/v1/me/appearance",
        headers={"If-Match": str(current.json()["version"])},
        json={
            "art_level": "standard",
            "reduce_motion": False,
            "theme_override": None,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "VERSION_CONFLICT"

    admin_current = client.get("/api/v1/admin/appearance")
    assert admin_current.status_code == 200, admin_current.text
    admin_updated = client.patch(
        "/api/v1/admin/appearance",
        headers={"If-Match": str(admin_current.json()["version"])},
        json={
            "theme_mode": "fixed",
            "fixed_theme": "autumn",
            "default_art_level": "standard",
            "default_reduce_motion": False,
        },
    )
    assert admin_updated.status_code == 200, admin_updated.text
    assert admin_updated.json()["fixed_theme"] == "autumn"


def test_rules_recommend_tasks_but_never_restricted_items(
    client: TestClient, admin: dict
) -> None:
    normal = create_task(
        client,
        admin["id"],
        title="1.3.0 临期规则建议",
        description="仅用于验证可解释排序。",
        steps=[],
        materials=[],
    )
    restricted = create_task(
        client,
        admin["id"],
        title="1.3.0 敏感事项不得推荐",
        description="",
        sensitivity="restricted",
        steps=[],
        materials=[],
    )
    response = client.get("/api/v1/ai/recommendations", params={"limit": 100})
    assert response.status_code == 200, response.text
    recommendations = response.json()
    assert any(item["object_id"] == normal["id"] for item in recommendations)
    assert all(item["object_id"] != restricted["id"] for item in recommendations)
    item = next(item for item in recommendations if item["object_id"] == normal["id"])
    assert item["generator"] == "rules"
    assert item["reason"]
    assert item["sources"][0]["id"] == normal["id"]

    accepted = client.post(
        f"/api/v1/ai/recommendations/{item['id']}/accept",
        headers={"If-Match": str(item["version"])},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    repeated = client.post(
        f"/api/v1/ai/recommendations/{item['id']}/accept",
        headers={"If-Match": str(accepted.json()["version"])},
    )
    assert repeated.status_code == 409
    assert repeated.json()["code"] == "AI_RECOMMENDATION_HANDLED"


def test_rule_recommendation_expires_when_task_version_changes(
    client: TestClient,
    admin: dict,
) -> None:
    task = create_task(
        client,
        admin["id"],
        title="版本变化会使旧建议失效",
        steps=[],
        materials=[],
    )
    before = client.get("/api/v1/ai/recommendations", params={"limit": 100})
    assert before.status_code == 200, before.text
    old = next(item for item in before.json() if item["object_id"] == task["id"])

    changed = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers={"If-Match": str(task["version"])},
        json={"title": "版本变化后的新标题"},
    )
    assert changed.status_code == 200, changed.text
    after = client.get("/api/v1/ai/recommendations", params={"limit": 100})
    assert after.status_code == 200, after.text
    current = [item for item in after.json() if item["object_id"] == task["id"]]
    assert current
    assert all(item["object_version"] == changed.json()["version"] for item in current)
    with db_runtime.session_factory() as db:
        expired = db.get(AIRecommendation, old["id"])
        assert expired is not None
        assert expired.status == RecommendationStatus.EXPIRED


def test_local_ai_missing_pack_degrades_without_affecting_business(
    client: TestClient, admin: dict
) -> None:
    status = client.get("/api/v1/ai/runtime/status")
    assert status.status_code == 200, status.text
    assert status.json()["ready"] is False
    assert status.json()["state"] in {"model_missing", "host_only"}

    task = create_task(
        client,
        admin["id"],
        title="本地模型缺失时业务仍可建档",
        steps=[],
        materials=[],
    )
    assert task["title"] == "本地模型缺失时业务仍可建档"


def test_model_pack_rejects_unsafe_member_path(
    client: TestClient, admin: dict
) -> None:
    payload = b"unsafe"
    files = {
        "../escape.gguf": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
        "embedding/model.onnx": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
        "embedding/tokenizer.json": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
        "LICENSE.txt": {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        },
    }
    manifest = {
        "format": "partyops-modelpack",
        "format_version": 1,
        "name": "恶意路径测试",
        "version": "1.0.0",
        "model_id": "test",
        "architecture": "universal",
        "components": {
            "llm": {"model_file": "../escape.gguf"},
            "embedding": {
                "model_file": "embedding/model.onnx",
                "tokenizer_file": "embedding/tokenizer.json",
            },
        },
        "license_files": ["LICENSE.txt"],
        "files": files,
        "signature": "",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name in files:
            archive.writestr(name, payload)
    response = client.post(
        "/api/v1/admin/ai/model-packs",
        files={
            "file": (
                "unsafe.partyops-modelpack",
                stream.getvalue(),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "MODEL_PACK_PATH_INVALID"


def _signed_model_pack_bytes(*, valid_signature: bool = True) -> bytes:
    """构造最小但结构完整的签名模型包，不依赖真实大模型文件。"""

    members = {
        "llm/qwen.gguf": b"gguf-test-model",
        "embedding/model.onnx": b"onnx-test-model",
        "embedding/tokenizer.json": b'{"version":"1.0"}',
        "LICENSE.txt": b"Qwen and BGE test licenses",
    }
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest = {
        "format": "partyops-modelpack",
        "format_version": 1,
        "name": "本地智能测试模型",
        "version": "1.0.0-test",
        "model_id": "qwen-test",
        "architecture": "universal",
        "components": {
            "llm": {"model_file": "llm/qwen.gguf"},
            "embedding": {
                "model_file": "embedding/model.onnx",
                "tokenizer_file": "embedding/tokenizer.json",
            },
        },
        "license_files": ["LICENSE.txt"],
        "files": {
            name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            for name, content in members.items()
        },
        "public_key": base64.b64encode(public_key).decode("ascii"),
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = private_key.sign(canonical)
    if not valid_signature:
        signature = bytes([signature[0] ^ 0x01]) + signature[1:]
    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


def _trust_model_pack(payload: bytes, monkeypatch) -> None:
    """测试只信任由外部配置注入的模型包公钥。"""

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    monkeypatch.setattr(
        get_settings(),
        "model_pack_public_key",
        str(manifest["public_key"]),
    )


def test_model_pack_signature_activation_and_resource_degradation(
    client: TestClient,
    admin: dict,
    monkeypatch,
) -> None:
    payload = _signed_model_pack_bytes()
    _trust_model_pack(payload, monkeypatch)
    imported = client.post(
        "/api/v1/admin/ai/model-packs",
        files={
            "file": (
                "signed-test.partyops-modelpack",
                payload,
                "application/octet-stream",
            )
        },
    )
    assert imported.status_code == 201, imported.text
    pack = imported.json()
    assert pack["signature_valid"] is True
    activated = client.post(
        f"/api/v1/admin/ai/model-packs/{pack['id']}/activate?capability=embedding"
    )
    assert activated.status_code == 200, activated.text
    activated_llm = client.post(
        f"/api/v1/admin/ai/model-packs/{pack['id']}/activate?capability=llm"
    )
    assert activated_llm.status_code == 200, activated_llm.text
    assert activated_llm.json()["active_capabilities"] == ["embedding", "llm"]

    monkeypatch.setattr("app.local_ai._available_memory_mb", lambda: 1024)
    memory_low = client.get("/api/v1/ai/runtime/status")
    assert memory_low.status_code == 200, memory_low.text
    assert memory_low.json()["state"] == "partial"
    assert memory_low.json()["embedding_available"] is True
    assert memory_low.json()["llm_available"] is False

    monkeypatch.setattr("app.local_ai._available_memory_mb", lambda: 8192)
    with db_runtime.session_factory() as db:
        job = BackgroundJob(job_type="backup", status="running", message="测试资源让行")
        db.add(job)
        db.commit()
        job_id = job.id
    paused = client.get("/api/v1/ai/runtime/status")
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "paused_busy"
    with db_runtime.session_factory() as db:
        job = db.get(BackgroundJob, job_id)
        assert job is not None
        db.delete(job)
        db.commit()

    monkeypatch.setattr("app.local_ai._embedding_runtime_available", lambda: True)
    monkeypatch.setattr("app.local_ai.LocalLlmRuntime._binary", staticmethod(lambda: None))
    runtime_missing = client.get("/api/v1/ai/runtime/status")
    assert runtime_missing.status_code == 200, runtime_missing.text
    assert runtime_missing.json()["ready"] is True
    assert runtime_missing.json()["state"] == "partial"
    assert runtime_missing.json()["embedding_available"] is True
    assert runtime_missing.json()["llm_available"] is False

    duplicate = client.post(
        "/api/v1/admin/ai/model-packs",
        files={
            "file": (
                "renamed.partyops-modelpack",
                payload,
                "application/octet-stream",
            )
        },
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["code"] == "MODEL_PACK_ALREADY_INSTALLED"

    with db_runtime.session_factory() as db:
        installed = db.get(AIModelPack, pack["id"])
        assert installed is not None
        llm_file = model_pack_root(installed) / "llm" / "qwen.gguf"
        llm_file.write_bytes(b"same-size-broken")
    corrupt = client.get("/api/v1/ai/runtime/status")
    assert corrupt.status_code == 200, corrupt.text
    assert corrupt.json()["state"] == "model_corrupt"


def test_model_pack_permission_error_degrades_status_instead_of_500(
    client: TestClient,
    admin: dict,
    monkeypatch,
) -> None:
    """模型文件暂时不可读时，更新页和系统诊断仍应正常打开。"""

    payload = _signed_model_pack_bytes()
    _trust_model_pack(payload, monkeypatch)
    imported = client.post(
        "/api/v1/admin/ai/model-packs",
        files={
            "file": (
                "permission-test.partyops-modelpack",
                payload,
                "application/octet-stream",
            )
        },
    )
    assert imported.status_code == 201, imported.text
    pack = imported.json()
    activated = client.post(
        f"/api/v1/admin/ai/model-packs/{pack['id']}/activate?capability=embedding"
    )
    assert activated.status_code == 200, activated.text

    with db_runtime.session_factory() as db:
        installed = db.get(AIModelPack, pack["id"])
        assert installed is not None
        denied_file = model_pack_root(installed) / "llm" / "qwen.gguf"

    original_is_file = Path.is_file

    def deny_one_model_file(path: Path) -> bool:
        if path == denied_file:
            raise PermissionError("test-only access denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", deny_one_model_file)

    runtime = client.get("/api/v1/ai/runtime/status")
    assert runtime.status_code == 200, runtime.text
    assert runtime.json()["state"] == "model_corrupt"

    system_status = client.get("/api/v1/admin/system-status")
    assert system_status.status_code == 200, system_status.text
    assert system_status.json()["ai"]["local"]["state"] == "model_corrupt"


def test_production_rejects_model_pack_with_invalid_signature(
    client: TestClient,
    admin: dict,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "model_pack_public_key", "")
    monkeypatch.setattr(settings, "update_public_key", "")
    response = client.post(
        "/api/v1/admin/ai/model-packs",
        files={
            "file": (
                "invalid-signature.partyops-modelpack",
                _signed_model_pack_bytes(valid_signature=False),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "MODEL_PACK_SIGNATURE_INVALID"


def test_0013_database_upgrades_and_downgrades_cleanly(tmp_path: Path) -> None:
    database = tmp_path / "upgrade-0013.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        for table in (
            "ai_recommendations",
            "semantic_index_checkpoints",
            "ai_model_packs",
            "user_appearance_preferences",
        ):
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")

        backend_root = Path(__file__).resolve().parents[1]
        config = Config(str(backend_root / "alembic.ini"))
        config.set_main_option("script_location", str(backend_root / "alembic"))
        config.attributes["connection"] = connection
        command.stamp(config, "0013")
        command.upgrade(config, "0015")

    tables = set(inspect(engine).get_table_names())
    assert {
        "user_appearance_preferences",
        "ai_model_packs",
        "semantic_index_checkpoints",
        "ai_recommendations",
    }.issubset(tables)

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0013")
    downgraded = set(inspect(engine).get_table_names())
    assert "user_appearance_preferences" not in downgraded
    assert "ai_model_packs" not in downgraded
