"""重要档案授权、字段、附件、作废恢复和业务关联发布闭环。"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from .conftest import create_task


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def test_archive_release_authorization_attachment_and_restore_lifecycle(
    client: TestClient,
    admin: dict,
    staff: dict,
) -> None:
    _login(client, "admin")
    suffix = uuid.uuid4().hex[:8]

    invalid_select = client.post(
        "/api/v1/archives/categories",
        json={
            "name": f"无选项类别-{suffix}",
            "code": f"invalid_{suffix}",
            "field_schema": [
                {"key": "result", "label": "结果", "type": "select", "options": []}
            ],
        },
    )
    assert invalid_select.status_code == 422
    assert invalid_select.json()["code"] == "ARCHIVE_FIELD_SCHEMA_INVALID"
    duplicate_field = client.post(
        "/api/v1/archives/categories",
        json={
            "name": f"重复字段类别-{suffix}",
            "code": f"duplicate_{suffix}",
            "field_schema": [
                {"key": "note", "label": "说明", "type": "text"},
                {"key": "note", "label": "说明二", "type": "textarea"},
            ],
        },
    )
    assert duplicate_field.status_code == 422

    category_response = client.post(
        "/api/v1/archives/categories",
        json={
            "name": f"开源发布档案-{suffix}",
            "code": f"release_{suffix}",
            "description": "验证指定人员贡献和完整附件闭环",
            "record_mode": "document",
            "field_schema": [
                {
                    "key": "level",
                    "label": "重要级别",
                    "type": "select",
                    "required": True,
                    "options": ["重要", "一般", "重要"],
                },
                {"key": "count", "label": "份数", "type": "number"},
                {"key": "date", "label": "归档日期", "type": "date"},
            ],
            "access_mode": "selected",
            "allow_device_access": True,
        },
    )
    assert category_response.status_code == 201, category_response.text
    category = category_response.json()
    assert category["field_schema"][0]["options"] == ["重要", "一般"]
    duplicate_category = client.post(
        "/api/v1/archives/categories",
        json={"name": category["name"], "code": f"other_{suffix}"},
    )
    assert duplicate_category.status_code == 409

    conflict = client.patch(
        f"/api/v1/archives/categories/{category['id']}",
        headers={"If-Match": "99"},
        json={"description": "冲突不应覆盖"},
    )
    assert conflict.status_code == 409
    patched = client.patch(
        f"/api/v1/archives/categories/{category['id']}",
        headers={"If-Match": str(category["version"])},
        json={"name": f"开源正式档案-{suffix}", "description": "已核对字段"},
    )
    assert patched.status_code == 200, patched.text
    category = patched.json()
    access = client.patch(
        f"/api/v1/archives/categories/{category['id']}/access",
        headers={"If-Match": str(category["version"])},
        json={"access_mode": "selected", "allow_device_access": True},
    )
    assert access.status_code == 200, access.text
    category = access.json()

    for payload, expected in (
        ({"can_view": True}, 422),
        ({"user_id": "missing-user", "can_view": True}, 404),
        ({"device_id": "missing-device", "can_view": True}, 404),
    ):
        response = client.post(
            f"/api/v1/archives/categories/{category['id']}/grants", json=payload
        )
        assert response.status_code == expected

    granted = client.post(
        f"/api/v1/archives/categories/{category['id']}/grants",
        json={
            "user_id": staff["id"],
            "can_view": True,
            "can_download": True,
            "can_contribute": True,
        },
    )
    assert granted.status_code == 201, granted.text
    grant = granted.json()
    repeated = client.post(
        f"/api/v1/archives/categories/{category['id']}/grants",
        json={
            "user_id": staff["id"],
            "can_view": True,
            "can_download": False,
            "can_contribute": True,
        },
    )
    assert repeated.status_code == 201 and repeated.json()["version"] == grant["version"] + 1
    grant = repeated.json()
    assert client.get(
        f"/api/v1/archives/categories/{category['id']}/grants"
    ).status_code == 200
    grant_conflict = client.patch(
        f"/api/v1/archives/categories/{category['id']}/grants/{grant['id']}",
        headers={"If-Match": "0"},
        json={"can_download": True},
    )
    assert grant_conflict.status_code == 409
    grant_enabled = client.patch(
        f"/api/v1/archives/categories/{category['id']}/grants/{grant['id']}",
        headers={"If-Match": str(grant["version"])},
        json={"can_download": True, "active": True},
    )
    assert grant_enabled.status_code == 200, grant_enabled.text

    _login(client, "staff")
    visible = client.get("/api/v1/archives/categories")
    assert any(item["id"] == category["id"] for item in visible.json())
    missing_required = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "title": "缺失动态必填字段",
            "custom_fields": {},
        },
    )
    assert missing_required.status_code == 422
    assert "custom_fields.level" in missing_required.json()["fields"]
    invalid_dynamic = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "title": "动态字段类型错误",
            "custom_fields": {"level": "无效", "count": "abc", "date": "not-date"},
        },
    )
    assert invalid_dynamic.status_code == 422

    created = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "document_no": f"党办〔2026〕{suffix}号",
            "title": "正式发布重要档案",
            "summary": "协同人员通过贡献授权录入",
            "tags": ["发布", "发布", " 稳定性 "],
            "custom_fields": {"level": "重要", "count": 2, "date": "2026-08-11"},
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["permissions"]["contribute"] is True
    assert record["tags"] == ["发布", "稳定性"]

    changed = client.patch(
        f"/api/v1/archives/records/{record['id']}",
        headers={"If-Match": str(record["version"])},
        json={"summary": "字段和附件准备完成", "change_note": "发布前复核"},
    )
    assert changed.status_code == 200, changed.text
    record = changed.json()
    uploaded = client.post(
        f"/api/v1/archives/records/{record['id']}/attachments",
        data={"note": "正式扫描件"},
        files={"file": ("发布扫描件.txt", b"partyops archive release", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    attachment = uploaded.json()
    listed = client.get(f"/api/v1/archives/records/{record['id']}/attachments")
    assert listed.status_code == 200 and listed.json()[0]["id"] == attachment["id"]
    downloaded = client.get(f"/api/v1/archives/attachments/{attachment['id']}/download")
    assert downloaded.status_code == 200 and downloaded.content == b"partyops archive release"

    _login(client, "admin")
    task = create_task(
        client,
        admin["id"],
        title=f"档案关联任务-{suffix}",
        steps=[],
        materials=[],
    )
    linked = client.post(
        f"/api/v1/archives/records/{record['id']}/links",
        json={"entity_type": "task", "entity_id": task["id"], "relation": "evidence"},
    )
    assert linked.status_code == 201, linked.text
    repeated_link = client.post(
        f"/api/v1/archives/records/{record['id']}/links",
        json={"entity_type": "task", "entity_id": task["id"], "relation": "evidence"},
    )
    assert repeated_link.status_code == 201 and len(repeated_link.json()["links"]) == 1
    missing_link = client.post(
        f"/api/v1/archives/records/{record['id']}/links",
        json={"entity_type": "task", "entity_id": "missing", "relation": "reference"},
    )
    assert missing_link.status_code == 404

    current = client.get(f"/api/v1/archives/records/{record['id']}").json()
    voided_attachment = client.post(
        f"/api/v1/archives/attachments/{attachment['id']}/void",
        headers={"If-Match": str(current["version"])},
        json={"reason": "以清晰版本替代"},
    )
    assert voided_attachment.status_code == 200, voided_attachment.text
    assert client.get(f"/api/v1/archives/attachments/{attachment['id']}/download").status_code == 410
    current = client.get(f"/api/v1/archives/records/{record['id']}").json()
    voided_record = client.post(
        f"/api/v1/archives/records/{record['id']}/void",
        headers={"If-Match": str(current["version"])},
        json={"reason": "发布门禁验证"},
    )
    assert voided_record.status_code == 200, voided_record.text
    assert client.get(f"/api/v1/archives/records/{record['id']}").status_code == 200
    restored = client.post(
        f"/api/v1/archives/records/{record['id']}/restore",
        headers={"If-Match": str(voided_record.json()["version"])},
        json={"reason": "复核通过恢复"},
    )
    assert restored.status_code == 200, restored.text
    history = client.get(f"/api/v1/archives/records/{record['id']}/history")
    assert history.status_code == 200 and len(history.json()) >= 3
    search = client.get("/api/v1/archives/search", params={"keyword": "正式发布重要档案"})
    assert search.status_code == 200 and any(item["id"] == record["id"] for item in search.json())
    export = client.get(
        "/api/v1/archives/export",
        params={"archive_year": 2026, "category_id": category["id"], "keyword": "正式发布"},
    )
    assert export.status_code == 200 and export.content.startswith(b"PK")
