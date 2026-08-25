"""备份、配对拉取、收件解析与导出包测试。"""

from __future__ import annotations

import io
import zipfile

from app.backups import verify_backup
from app.config import get_settings
from app.problems import ProblemException
from fastapi.testclient import TestClient


def test_intake_text_is_candidate_only(client: TestClient, admin: dict) -> None:
    response = client.post(
        "/api/v1/intake/parse",
        data={
            "pasted_text": "关于报送八月工作台账的通知\n请于2026年8月20日前提交材料。"
        },
    )
    assert response.status_code == 200
    candidate = response.json()
    assert candidate["title"].startswith("关于报送")
    assert candidate["formal_due_at"].startswith("2026-08-20")
    assert candidate["requirements"]


def test_backup_manifest_pairing_and_admin_download(
    client: TestClient, admin: dict
) -> None:
    created = client.post("/api/v1/backups")
    assert created.status_code == 201, created.text
    backup = created.json()
    path = get_settings().backups_dir / backup["filename"]
    manifest = verify_backup(path)
    assert manifest["format"] == "partyops-backup"
    assert manifest["schema_version"] == "0024"

    admin_download = client.get(f"/api/v1/backups/{backup['id']}/download")
    assert admin_download.status_code == 200
    assert admin_download.content.startswith(b"PK")

    pairing = client.post(
        "/api/v1/admin/pairings",
        json={"name": "测试协同终端"},
    )
    assert pairing.status_code == 201
    token = pairing.json()["token"]
    terminal = client.get(
        "/api/v1/backups/latest",
        headers={"X-PartyOps-Pairing": token},
    )
    assert terminal.status_code == 200
    assert terminal.content.startswith(b"PK")
    verified = client.post(
        "/api/v1/admin/backups/verify",
        files={
            "file": (
                backup["filename"],
                path.read_bytes(),
                "application/zip",
            )
        },
    )
    assert verified.status_code == 200
    assert verified.json()["valid"] is True


def test_corrupted_backup_is_rejected(client: TestClient, admin: dict, tmp_path) -> None:
    created = client.post("/api/v1/backups").json()
    source = get_settings().backups_dir / created["filename"]
    corrupt = tmp_path / "corrupt.partyops-backup"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(corrupt, "w") as output:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "database/partyops.db":
                data += b"corrupt"
            output.writestr(info, data)
    try:
        verify_backup(corrupt)
        assert False, "损坏备份必须被拒绝"
    except ProblemException as error:
        assert error.code == "BACKUP_HASH_MISMATCH"


def test_inspection_package_contains_manifest(client: TestClient, admin: dict) -> None:
    response = client.get("/api/v1/inspection/package")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert "校验清单.json" in names
    assert "材料目录.xlsx" in names
