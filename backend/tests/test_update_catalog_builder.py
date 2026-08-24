"""官网在线更新目录生成器的签名与平台矩阵回归。"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate-update-catalog.py"
SPEC = importlib.util.spec_from_file_location("partyops_update_catalog_builder", SCRIPT)
assert SPEC and SPEC.loader
catalog_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog_builder)


def _keys(tmp_path: Path) -> tuple[Path, Path, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "private.pem"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path = tmp_path / "public.txt"
    public_path.write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
        encoding="ascii",
    )
    return private_path, public_path, key


def test_catalog_selectors_are_complete_and_signature_is_verifiable(tmp_path: Path) -> None:
    private_path, public_path, key = _keys(tmp_path)
    for platform_name, architecture in catalog_builder.TARGETS:
        (tmp_path / f"partyops_{catalog_builder.VERSION}_{platform_name}_{architecture}.partyops-update").write_bytes(
            f"{platform_name}/{architecture}".encode()
        )
    catalog = catalog_builder.generate_catalog(
        packages_dir=tmp_path,
        private_key_path=private_path,
        public_key_path=public_path,
        package_base_url="https://www.partyops.cn/releases",
        published_at="2026-08-15T14:30:00+08:00",
    )
    assert catalog["format_version"] == 3
    release = catalog["release"]
    assert isinstance(release, dict)
    assert release["min_version"] == catalog_builder.VERSION
    packages = release["platform_packages"]
    assert isinstance(packages, dict)
    assert set(packages) == {platform for platform, _architecture in catalog_builder.TARGETS}
    assert packages["windows7"]["x86"]["package_url"].endswith("windows7_x86.partyops-update")
    signature = base64.b64decode(str(catalog["signature"]), validate=True)
    unsigned = dict(catalog)
    unsigned.pop("signature")
    canonical = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    key.public_key().verify(signature, canonical)


def test_catalog_rejects_unsafe_url_timezone_and_wrong_key(tmp_path: Path) -> None:
    private_path, public_path, _key = _keys(tmp_path)
    for platform_name, architecture in catalog_builder.TARGETS:
        (tmp_path / f"partyops_{catalog_builder.VERSION}_{platform_name}_{architecture}.partyops-update").write_bytes(b"x")
    common = {
        "packages_dir": tmp_path,
        "private_key_path": private_path,
        "public_key_path": public_path,
        "package_base_url": "https://www.partyops.cn/releases",
        "published_at": "2026-08-15T14:30:00+08:00",
    }
    with pytest.raises(ValueError, match="时区"):
        catalog_builder.generate_catalog(**{**common, "published_at": "2026-08-15T14:30:00"})
    with pytest.raises(ValueError, match="HTTPS"):
        catalog_builder.generate_catalog(**{**common, "package_base_url": "http://www.partyops.cn/releases"})
    public_path.write_text(base64.b64encode(b"0" * 32).decode(), encoding="ascii")
    with pytest.raises(ValueError, match="不匹配"):
        catalog_builder.generate_catalog(**common)


def test_catalog_can_publish_only_independently_eligible_targets(tmp_path: Path) -> None:
    private_path, public_path, _key = _keys(tmp_path)
    targets = (("windows", "amd64"), ("linux-deb", "arm64"))
    for platform_name, architecture in targets:
        (tmp_path / f"partyops_{catalog_builder.VERSION}_{platform_name}_{architecture}.partyops-update").write_bytes(b"eligible")
    catalog = catalog_builder.generate_catalog(
        packages_dir=tmp_path,
        private_key_path=private_path,
        public_key_path=public_path,
        package_base_url="https://www.partyops.cn/releases",
        published_at="2026-08-15T15:00:00+08:00",
        targets=targets,
    )
    packages = catalog["release"]["platform_packages"]
    assert set(packages) == {"windows", "linux-deb"}
    assert set(packages["windows"]) == {"amd64"}
    assert set(packages["linux-deb"]) == {"arm64"}

    for invalid in ((), (("windows", "amd64"), ("windows", "amd64")), (("unknown", "amd64"),)):
        with pytest.raises(ValueError, match="目标"):
            catalog_builder.generate_catalog(
                packages_dir=tmp_path,
                private_key_path=private_path,
                public_key_path=public_path,
                package_base_url="https://www.partyops.cn/releases",
                published_at="2026-08-15T15:00:00+08:00",
                targets=invalid,
            )


def test_catalog_target_parser_rejects_unknown_and_duplicate_values() -> None:
    assert catalog_builder.resolve_targets(None) == catalog_builder.TARGETS
    assert catalog_builder.resolve_targets(["windows/amd64"]) == (("windows", "amd64"),)
    for values in (["missing"], ["windows/amd64", "windows/amd64"]):
        with pytest.raises(ValueError):
            catalog_builder.resolve_targets(values)


def test_catalog_accepts_independent_cloud_studio_urls(tmp_path: Path) -> None:
    private_path, public_path, _key = _keys(tmp_path)
    targets = (("windows", "amd64"), ("linux-deb", "arm64"))
    url_map: dict[str, str] = {}
    for platform_name, architecture in targets:
        filename = f"partyops_{catalog_builder.VERSION}_{platform_name}_{architecture}.partyops-update"
        (tmp_path / filename).write_bytes(b"eligible")
        url_map[f"{platform_name}/{architecture}"] = (
            f"https://release.example.cn/downloads/{filename}"
        )
    catalog = catalog_builder.generate_catalog(
        packages_dir=tmp_path,
        private_key_path=private_path,
        public_key_path=public_path,
        package_base_url=None,
        package_url_map=url_map,
        published_at="2026-08-24T19:00:00+08:00",
        targets=targets,
    )
    assert (
        catalog["release"]["platform_packages"]["linux-deb"]["arm64"]["package_url"]
        == url_map["linux-deb/arm64"]
    )

    invalid_maps = (
        {"windows/amd64": url_map["windows/amd64"]},
        {**url_map, "linux-deb/arm64": "http://release.example.cn/downloads/file"},
        {**url_map, "linux-deb/arm64": "https://release.example.cn/files/wrong.partyops-update"},
    )
    for invalid_map in invalid_maps:
        with pytest.raises(ValueError, match="地址"):
            catalog_builder.generate_catalog(
                packages_dir=tmp_path,
                private_key_path=private_path,
                public_key_path=public_path,
                package_base_url=None,
                package_url_map=invalid_map,
                published_at="2026-08-24T19:00:00+08:00",
                targets=targets,
            )


def test_catalog_rejects_ambiguous_or_missing_url_source(tmp_path: Path) -> None:
    private_path, public_path, _key = _keys(tmp_path)
    filename = f"partyops_{catalog_builder.VERSION}_windows_amd64.partyops-update"
    (tmp_path / filename).write_bytes(b"eligible")
    common = {
        "packages_dir": tmp_path,
        "private_key_path": private_path,
        "public_key_path": public_path,
        "published_at": "2026-08-24T19:00:00+08:00",
        "targets": (("windows", "amd64"),),
    }
    with pytest.raises(ValueError, match="必须且只能"):
        catalog_builder.generate_catalog(
            **common,
            package_base_url=None,
            package_url_map=None,
        )
    with pytest.raises(ValueError, match="必须且只能"):
        catalog_builder.generate_catalog(
            **common,
            package_base_url="https://www.partyops.cn/releases",
            package_url_map={
                "windows/amd64": f"https://release.example.cn/downloads/{filename}"
            },
        )
