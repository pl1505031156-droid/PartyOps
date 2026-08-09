"""重要档案中心和目录自动识别回归测试。"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.archive_service import index_archive_attachment

def _category(client: TestClient, name: str) -> dict:
    response = client.get("/api/v1/archives/categories")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["name"] == name)


def test_archive_custom_year_crud_search_history_export_and_attachment(
    client: TestClient, admin: dict,
) -> None:
    assert admin["role"] == "admin"
    category = _category(client, "人事调动文件")
    created = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 1998,
            "title": "历史人事调动文件",
            "document_no": "宣组干（1998）001号",
            "summary": "历史年度档案扫描件",
            "involved_persons": ["张三", "李四"],
            "document_date": "1998-03-01T00:00:00+08:00",
            "tags": ["历史", "人事"],
        },
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["archive_year"] == 1998
    assert record["sequence_no"] == 1
    assert len(record["attachments"]) == 0

    duplicate = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 1998,
            "sequence_no": 1,
            "title": "重复序号",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ARCHIVE_SEQUENCE_EXISTS"

    upload = client.post(
        f"/api/v1/archives/records/{record['id']}/attachments",
        files={"file": ("调动扫描件.txt", io.BytesIO("历史扫描件正文".encode()), "text/plain")},
        data={"note": "原始扫描件"},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    if attachment["status"] == "pending_ocr":
        index_archive_attachment(attachment["id"])
        attachment = client.get(
            f"/api/v1/archives/records/{record['id']}/attachments"
        ).json()[0]
    assert attachment["status"] == "indexed"
    assert attachment["size_bytes"] == len("历史扫描件正文".encode())
    assert attachment["blob_sha256"] == hashlib.sha256("历史扫描件正文".encode()).hexdigest()

    detail = client.get(f"/api/v1/archives/records/{record['id']}")
    assert detail.status_code == 200
    assert detail.json()["attachment_count"] == 1
    assert client.get("/api/v1/archives/search", params={"keyword": "历史扫描件正文"}).json()[0]["id"] == record["id"]

    changed = client.patch(
        f"/api/v1/archives/records/{record['id']}",
        headers={"If-Match": str(detail.json()["version"])},
        json={"summary": "更正后的历史摘要", "change_note": "补充历史摘要"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["summary"] == "更正后的历史摘要"
    history = client.get(f"/api/v1/archives/records/{record['id']}/history")
    assert history.status_code == 200
    assert len(history.json()) >= 2

    years = client.get("/api/v1/archives/years")
    assert years.status_code == 200
    assert any(item["year"] == 1998 for item in years.json()["years"])

    export = client.get("/api/v1/archives/export", params={"archive_year": 1998})
    assert export.status_code == 200, export.text
    with zipfile.ZipFile(io.BytesIO(export.content)) as package:
        names = set(package.namelist())
        assert "manifest.json" in names
        assert "SHA256SUMS" in names
        assert any(name.endswith("调动扫描件.txt") for name in names)


def test_workspace_root_creation_automatically_indexes_names_only(
    client: TestClient, admin: dict, tmp_path: Path
) -> None:
    root_path = tmp_path / "自动识别资料"
    nested = root_path / "2024年" / "考核材料"
    nested.mkdir(parents=True)
    source = nested / "公务员年度考核结果.txt"
    source.write_text("自动识别正文：年度考核优秀", encoding="utf-8")
    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "自动识别资料目录", "absolute_path": str(root_path)},
    )
    assert created.status_code == 201, created.text
    root = created.json()
    assert root["scan_status"] in {"pending", "running", "completed", "completed_with_errors"}
    found = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "公务员年度考核结果"},
    )
    assert found.status_code == 200, found.text
    assert any(item["name"] == source.name for item in found.json())
    body_search = client.get(
        "/api/v1/workspace/search",
        params={"root_id": root["id"], "keyword": "自动识别正文"},
    )
    assert body_search.status_code == 200
    assert body_search.json() == []


def test_archive_category_access_and_staff_contribution(
    client: TestClient, admin: dict, staff: dict
) -> None:
    category = _category(client, "其他重要文件")
    record = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2035,
            "title": "未来年度重要工作文件",
        },
    ).json()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    )
    assert login.status_code == 200
    detail = client.get(f"/api/v1/archives/records/{record['id']}")
    assert detail.status_code == 200
    assert detail.json()["permissions"]["contribute"] is True
    contributed = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2035,
            "title": "协同人员录入的重要档案",
        },
    )
    assert contributed.status_code == 201, contributed.text
    changed = client.patch(
        f"/api/v1/archives/records/{contributed.json()['id']}",
        headers={"If-Match": str(contributed.json()["version"])},
        json={"summary": "协同人员补充摘要", "change_note": "补齐测试材料"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["summary"] == "协同人员补充摘要"

    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    ).status_code == 200
    restricted = client.patch(
        f"/api/v1/archives/categories/{category['id']}/access",
        headers={"If-Match": str(category["version"])},
        json={"access_mode": "admins_only", "allow_device_access": False},
    )
    assert restricted.status_code == 200, restricted.text

    assert client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    ).status_code == 200
    assert client.get(f"/api/v1/archives/records/{record['id']}").status_code == 403
    assert not any(
        item["id"] == category["id"]
        for item in client.get("/api/v1/archives/categories").json()
    )

    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    ).status_code == 200
    opened = client.patch(
        f"/api/v1/archives/categories/{category['id']}/access",
        headers={"If-Match": str(restricted.json()["version"])},
        json={"access_mode": "all_users", "allow_device_access": True},
    )
    assert opened.status_code == 200


def test_selected_archive_requires_explicit_contribution_grant(
    client: TestClient, admin: dict, staff: dict
) -> None:
    category = client.post(
        "/api/v1/archives/categories",
        json={
            "name": "指定人员协同档案",
            "code": "selected_collaboration_archive",
            "record_mode": "document",
            "access_mode": "selected",
            "allow_device_access": True,
        },
    )
    assert category.status_code == 201, category.text
    category_id = category.json()["id"]
    view_grant = client.post(
        f"/api/v1/archives/categories/{category_id}/grants",
        json={
            "user_id": staff["id"],
            "can_view": True,
            "can_download": True,
            "can_contribute": False,
        },
    )
    assert view_grant.status_code == 201, view_grant.text
    client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    )
    denied = client.post(
        "/api/v1/archives/records",
        json={"category_id": category_id, "archive_year": 2036, "title": "未授权贡献"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "ARCHIVE_CONTRIBUTE_DENIED"

    client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    grants = client.get(f"/api/v1/archives/categories/{category_id}/grants")
    assert grants.status_code == 200, grants.text
    grant = grants.json()[0]
    enabled = client.patch(
        f"/api/v1/archives/categories/{category_id}/grants/{grant['id']}",
        headers={"If-Match": str(grant["version"])},
        json={"can_contribute": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["can_contribute"] is True


def test_archive_custom_fields_and_person_year_uniqueness(
    client: TestClient, admin: dict
) -> None:
    category = client.post(
        "/api/v1/archives/categories",
        json={
            "name": "重要会议文件",
            "code": "important_meeting_files",
            "description": "按年度保存重要会议材料",
            "record_mode": "document",
            "field_schema": [
                {
                    "key": "security_level",
                    "label": "保管级别",
                    "type": "select",
                    "required": True,
                    "options": ["长期", "永久"],
                }
            ],
            "access_mode": "admins_only",
            "allow_device_access": False,
        },
    )
    assert category.status_code == 201, category.text
    missing = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category.json()["id"],
            "archive_year": 2010,
            "title": "重要会议纪要",
            "custom_fields": {},
        },
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "ARCHIVE_FIELD_REQUIRED"

    dated = client.post(
        "/api/v1/archives/categories",
        json={
            "name": "日期字段校验档案",
            "code": "archive_date_validation",
            "record_mode": "document",
            "field_schema": [
                {"key": "effective_date", "label": "生效日期", "type": "date", "required": True}
            ],
            "access_mode": "all_users",
            "allow_device_access": True,
        },
    )
    assert dated.status_code == 201, dated.text
    invalid_date = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": dated.json()["id"],
            "archive_year": 2026,
            "title": "无效日期",
            "custom_fields": {"effective_date": "2026-02-30"},
        },
    )
    assert invalid_date.status_code == 422, invalid_date.text
    assert invalid_date.json()["fields"]["custom_fields.effective_date"] == "请选择有效日期"

    assessment = _category(client, "公务员年度考核")
    invalid_assessment = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": assessment["id"],
            "archive_year": 2024,
            "title": "错误考核选项",
            "person_name": "测试人员",
            "assessment_result": "非常优秀",
        },
    )
    assert invalid_assessment.status_code == 422
    assert invalid_assessment.json()["code"] == "ARCHIVE_FIELD_INVALID"
    assert invalid_assessment.json()["fields"]["assessment_result"]
    first = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": assessment["id"],
            "archive_year": 2024,
            "title": "王某 2024 年度考核",
            "person_name": "王某",
            "person_identifier": "GWY-001",
        },
    )
    assert first.status_code == 201, first.text
    duplicate = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": assessment["id"],
            "archive_year": 2024,
            "title": "重复人员考核",
            "person_name": "王某",
            "person_identifier": "GWY-001",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ARCHIVE_PERSON_EXISTS"
