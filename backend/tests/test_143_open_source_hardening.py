"""开源发布前的安全边界回归测试。"""

from __future__ import annotations

import base64
import json
import stat
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings, get_settings
from app.backups import _safe_zip_members, verify_backup
from app.intake import extract_path_content
from app.model_packs import _manifest_signature_valid as model_signature_valid
from app.routers.updates import _manifest_signature_valid as update_signature_valid
from app.problems import ProblemException
from app.spreadsheet_security import safe_spreadsheet_cell, safe_spreadsheet_row


def test_presentation_xml_entities_are_rejected(tmp_path) -> None:
    """回归：不可信 Office XML 不能触发实体扩展。"""

    presentation = tmp_path / "unsafe.pptx"
    with zipfile.ZipFile(presentation, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0"?>
<!DOCTYPE slide [<!ENTITY payload "expanded">]>
<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>&payload;</a:t></p:sld>
""",
        )

    result = extract_path_content(presentation)

    assert result.content_status == "error"
    assert result.error_code == "CONTENT_PARSE_FAILED"
    assert "expanded" not in result.text


def test_default_environment_is_fail_secure() -> None:
    assert Settings.model_fields["environment"].default == "production"


def test_package_cannot_trust_its_own_signing_key(monkeypatch) -> None:
    """回归：包内自声明公钥不能把恶意更新或可执行模型变成可信包。"""

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest = {
        "format": "partyops-update",
        "format_version": 2,
        "version": "9.9.9",
        "public_key": base64.b64encode(public_key).decode("ascii"),
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(private_key.sign(canonical)).decode("ascii")
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(settings, "update_public_key", "")
    monkeypatch.setattr(settings, "model_pack_public_key", "")

    assert update_signature_valid(manifest) is False
    assert model_signature_valid(manifest) is False


def test_backup_rejects_symlink_member(tmp_path) -> None:
    archive_path = tmp_path / "symlink.partyops-backup"
    with zipfile.ZipFile(archive_path, "w") as archive:
        link = zipfile.ZipInfo("attachments/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
    with zipfile.ZipFile(archive_path) as archive:
        try:
            _safe_zip_members(archive, tmp_path / "output")
        except ProblemException as exc:
            assert exc.code == "BACKUP_PATH_INVALID"
        else:  # pragma: no cover - 安全回归失败时给出清晰断言
            raise AssertionError("符号链接成员必须被拒绝")


def test_backup_rejects_non_array_manifest_files(tmp_path) -> None:
    archive_path = tmp_path / "invalid.partyops-backup"
    manifest = {
        "format": "partyops-backup",
        "format_version": 1,
        "schema_version": "0017",
        "files": {"path": "database/partyops.db"},
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
    try:
        verify_backup(archive_path)
    except ProblemException as exc:
        assert exc.code == "BACKUP_MANIFEST_INVALID"
    else:  # pragma: no cover
        raise AssertionError("非数组文件清单必须被拒绝")


def test_spreadsheet_cells_never_export_untrusted_formulas() -> None:
    values = ["=HYPERLINK(\"https://attacker.invalid\")", " +1+1", "@SUM(A1:A2)", "正常文字", 7]
    protected = safe_spreadsheet_row(values)
    assert protected[:3] == [
        "'=HYPERLINK(\"https://attacker.invalid\")",
        "' +1+1",
        "'@SUM(A1:A2)",
    ]
    assert protected[3:] == ["正常文字", 7]
    assert safe_spreadsheet_cell("-2+3") == "'-2+3"
