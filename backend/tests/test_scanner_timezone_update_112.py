"""1.1.2 全类型扫描、时区和更新包契约回归。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import intake
from .conftest import create_task


def test_parser_type_error_isolated_to_one_file(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "WPS异常表格.xlsx"
    source.write_bytes(b"PK\x03\x04broken")

    def broken_loader(*_args, **_kwargs):
        raise TypeError("WPS generated invalid workbook")

    monkeypatch.setattr(intake, "load_workbook", broken_loader)
    result = intake.extract_path_content(source)
    assert result.content_status == "error"
    assert result.error_code == "CONTENT_PARSE_FAILED"
    assert "TypeError" not in " ".join(result.warnings)


def test_all_file_types_are_kept_without_reading_content(
    client: TestClient,
    admin: dict,
    tmp_path: Path,
) -> None:
    root_path = tmp_path / "全类型资料"
    root_path.mkdir()
    (root_path / "旧版公文.wps").write_bytes(b"WPS proprietary")
    (root_path / "未知文件").write_bytes(b"\x00\x01\x02")
    (root_path / "数据.bin").write_bytes(b"\x10\x11\x12")
    with zipfile.ZipFile(root_path / "资料.zip", "w") as archive:
        archive.writestr("年度/工作清单.txt", "目录内容不自动释放")

    created = client.post(
        "/api/v1/workspace/roots",
        json={"name": "全类型扫描", "absolute_path": str(root_path.resolve())},
    )
    assert created.status_code == 201, created.text
    root_id = created.json()["id"]

    late_file = root_path / "单文件解析异常.et"
    late_file.write_bytes(b"PK\x03\x04broken")
    scanned = client.post(f"/api/v1/workspace/roots/{root_id}/scan-now")
    assert scanned.status_code == 200, scanned.text
    summary = scanned.json()
    assert summary["files"] == 5
    assert summary["content_failed"] == 0
    assert summary["metadata_only"] == 5
    assert summary["diagnostic_id"]
    assert all("TypeError" not in warning for warning in summary["errors"])

    listed = client.get(
        "/api/v1/workspace/files",
        params={"root_id": root_id, "limit": 100},
    )
    assert listed.status_code == 200, listed.text
    by_name = {item["name"]: item for item in listed.json()}
    assert set(by_name) == {
        "旧版公文.wps",
        "未知文件",
        "数据.bin",
        "资料.zip",
        "单文件解析异常.et",
    }
    assert by_name["单文件解析异常.et"]["content_status"] == "metadata_only"
    assert by_name["资料.zip"]["archive_member_count"] == 0


def test_legacy_naive_utc_is_serialized_with_z(client: TestClient, admin: dict) -> None:
    response = client.post(
        "/api/v1/work-journal",
        json={"title": "时区契约", "content": "验证接口统一返回 UTC 标记。"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["created_at"].endswith("Z")
    assert payload["occurred_at"].endswith("Z")


def test_dictionary_aggregate_times_also_use_utc_z(
    client: TestClient,
    admin: dict,
) -> None:
    task = create_task(
        client,
        admin["id"],
        title="字典接口时区契约",
        task_type="quick",
        steps=[],
        materials=[],
    )
    workbench = client.get("/api/v1/workbench")
    assert workbench.status_code == 200, workbench.text
    assert workbench.json()["updated_at"].endswith("Z")

    searched = client.get(
        "/api/v1/global-search",
        params={"q": task["title"]},
    )
    assert searched.status_code == 200, searched.text
    item = next(result for result in searched.json()["items"] if result["id"] == task["id"])
    assert item["updated_at"].endswith("Z")


def test_update_format_v1_is_rejected_without_exposing_internals(
    client: TestClient,
    admin: dict,
) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "partyops-update",
                    "version": "1.1.3",
                    "min_version": "1.1.1",
                    "schema_revision": "0009",
                    "artifacts": {"partyops_1.1.3_amd64.deb": {}},
                }
            ),
        )
    response = client.post(
        "/api/v1/admin/updates/upload",
        files={
            "file": (
                "legacy.partyops-update",
                stream.getvalue(),
                "application/octet-stream",
            )
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UPDATE_FORMAT_VERSION_UNSUPPORTED"
    assert "Traceback" not in response.text
