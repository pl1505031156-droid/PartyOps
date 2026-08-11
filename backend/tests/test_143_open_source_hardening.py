"""开源发布前的安全边界回归测试。"""

from __future__ import annotations

import base64
import json
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import Settings, get_settings
from app.intake import extract_path_content
from app.model_packs import _manifest_signature_valid as model_signature_valid
from app.routers.updates import _manifest_signature_valid as update_signature_valid


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
