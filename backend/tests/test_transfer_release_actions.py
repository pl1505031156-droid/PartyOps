"""受管接收箱的下载、固化、转档案和转任务材料真实闭环。"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import db_runtime
from app.models import Transfer, utcnow
from app.routers.fleet import safe_name

from .conftest import create_task


def _completed_transfer(admin_id: str, content: bytes, name: str) -> str:
    digest = hashlib.sha256(content).hexdigest()
    with db_runtime.session_factory() as db:
        transfer = Transfer(
            direction="device_to_host",
            status="completed",
            original_name=name,
            relative_path=name,
            size_bytes=len(content),
            sha256=digest,
            result_sha256=digest,
            chunk_size=8 * 1024 * 1024,
            total_chunks=1,
            completed_chunks=1,
            requested_by=admin_id,
            expires_at=utcnow() + timedelta(days=7),
            delivery_mode="browser",
            bundle_mode="single",
            result_name=name,
        )
        db.add(transfer)
        db.commit()
        db.refresh(transfer)
        transfer_id = transfer.id
    target = get_settings().inbox_dir / f"{transfer_id}-{safe_name(name)}"
    target.write_bytes(content)
    return transfer_id


def test_completed_inbox_freeze_archive_and_material_actions(
    client: TestClient,
    admin: dict,
) -> None:
    assert client.get("/api/v1/transfers/missing/content").status_code == 404

    freeze_id = _completed_transfer(admin["id"], b"freeze-release", "固化材料.txt")
    frozen = client.post(f"/api/v1/transfers/{freeze_id}/freeze")
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["linked_entity_type"] == "frozen"
    downloaded = client.get(f"/api/v1/transfers/{freeze_id}/content", params={"inline": True})
    assert downloaded.status_code == 200 and downloaded.content == b"freeze-release"

    category = next(
        item
        for item in client.get("/api/v1/archives/categories").json()
        if item["access_mode"] == "all_users" and item["record_mode"] == "document"
    )
    record = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "title": "接收箱转重要档案扫描件",
            "document_no": "发布附件〔2026〕1号",
        },
    )
    assert record.status_code == 201, record.text
    archive_id = _completed_transfer(admin["id"], b"archive-scan", "档案扫描件.txt")
    attached_archive = client.post(
        f"/api/v1/transfers/{archive_id}/attach",
        json={
            "target_type": "archive",
            "target_id": record.json()["id"],
            "note": "从协同机接收箱转入",
            "stage": "draft",
            "is_final": False,
        },
    )
    assert attached_archive.status_code == 200, attached_archive.text
    assert attached_archive.json()["linked_entity_type"] == "archive"
    attachments = client.get(
        f"/api/v1/archives/records/{record.json()['id']}/attachments"
    )
    assert attachments.status_code == 200 and attachments.json()[0]["display_name"] == "档案扫描件.txt"

    task = create_task(client, admin["id"], title="接收箱转任务材料")
    material_id = task["materials"][0]["id"]
    material_transfer = _completed_transfer(
        admin["id"], b"task-material", "协同任务材料.txt"
    )
    invalid_final = client.post(
        f"/api/v1/transfers/{material_transfer}/attach",
        json={
            "target_type": "task_material",
            "target_id": material_id,
            "note": "错误终稿阶段",
            "stage": "draft",
            "is_final": True,
        },
    )
    assert invalid_final.status_code == 422
    attached_material = client.post(
        f"/api/v1/transfers/{material_transfer}/attach",
        json={
            "target_type": "task_material",
            "target_id": material_id,
            "note": "协同机接收材料",
            "stage": "revision",
            "is_final": False,
        },
    )
    assert attached_material.status_code == 200, attached_material.text
    assert attached_material.json()["linked_entity_type"] == "task_material"
    task_detail = client.get(f"/api/v1/tasks/{task['id']}")
    versions = task_detail.json()["materials"][0]["versions"]
    assert versions[0]["original_name"] == "协同任务材料.txt"


def test_completed_inbox_revalidates_owner_size_hash_and_readiness(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    ready_id = _completed_transfer(admin["id"], b"owner-only", "仅发起人.txt")
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    )
    assert login.status_code == 200
    assert client.get(f"/api/v1/transfers/{ready_id}/content").status_code == 403
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    ).status_code == 200

    with db_runtime.session_factory() as db:
        not_ready = Transfer(
            direction="device_to_host",
            status="queued",
            original_name="尚未完成.txt",
            size_bytes=1,
            requested_by=admin["id"],
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(not_ready)
        db.commit()
        db.refresh(not_ready)
        not_ready_id = not_ready.id
    response = client.get(f"/api/v1/transfers/{not_ready_id}/content")
    assert response.status_code == 409 and response.json()["code"] == "INBOX_FILE_NOT_READY"

    size_id = _completed_transfer(admin["id"], b"size", "大小错误.txt")
    size_path = get_settings().inbox_dir / f"{size_id}-{safe_name('大小错误.txt')}"
    size_path.write_bytes(b"changed-size")
    response = client.get(f"/api/v1/transfers/{size_id}/content")
    assert response.status_code == 409 and response.json()["code"] == "INBOX_FILE_SIZE_MISMATCH"

    hash_id = _completed_transfer(admin["id"], b"hash-a", "哈希错误.txt")
    hash_path = get_settings().inbox_dir / f"{hash_id}-{safe_name('哈希错误.txt')}"
    hash_path.write_bytes(b"hash-b")
    response = client.get(f"/api/v1/transfers/{hash_id}/content")
    assert response.status_code == 409 and response.json()["code"] == "INBOX_FILE_HASH_MISMATCH"
