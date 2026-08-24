from __future__ import annotations

import importlib.util
import base64
import json
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate-release-bundle-manifest.py"
SPEC = importlib.util.spec_from_file_location("release_bundle_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _installers(root: Path) -> None:
    for name in MODULE.INSTALLERS:
        (root / name).write_bytes(name.encode("utf-8"))


def _release_key(root: Path) -> tuple[Path, Path, Ed25519PrivateKey]:
    root.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private = root / "release-private.pem"
    public = root / "release-public.txt"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
        encoding="ascii",
    )
    return private, public, key


def test_manifest_requires_only_current_installers(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _installers(artifacts)
    private, public, key = _release_key(tmp_path / "keys")
    output = artifacts / "PartyOps_1.4.5-rc.2_release-manifest.json"
    payload = MODULE.build_manifest(
        root=artifacts,
        output=output,
        source_commit="a" * 40,
        tooling_commit="b" * 40,
        macos_source_commit="c" * 40,
        macos_workflow_commit="d" * 40,
        macos_build_run="32681880115",
        generated_at="2026-08-18T10:00:00+08:00",
        private_key_path=private,
        public_key_path=public,
        verified_platforms=("windows/amd64",),
        native_verified_platforms=("macos/amd64", "macos/arm64"),
        emulated_verified_platforms=("linux-deb/arm64", "linux-rpm/arm64"),
    )
    assert payload["schema_version"] == 4
    assert payload["prerelease"] is True
    assert payload["make_latest"] is False
    assert payload["signed"] is True
    assert payload["packaged_platforms"] == list(MODULE.INSTALLERS.values())
    assert payload["verified_platforms"] == ["windows/amd64"]
    assert payload["native_verified_platforms"] == ["macos/amd64", "macos/arm64"]
    assert payload["unavailable_platforms"] == []
    assert payload["supplemental_sources"] == [
        {
            "scope": ["macos/amd64", "macos/arm64"],
            "source_commit": "c" * 40,
            "workflow_commit": "d" * 40,
            "native_build_run": "32681880115",
        }
    ]
    # 测试密钥文件在生产发布目录中不会存在；这里仅验证安装包资产签名。
    installer_assets = [asset for asset in payload["assets"] if asset["filename"] in MODULE.INSTALLERS]
    assert {asset["filename"] for asset in installer_assets} == set(MODULE.INSTALLERS)
    unsigned = dict(payload)
    signature = base64.b64decode(str(unsigned.pop("signature")))
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    key.public_key().verify(signature, canonical)


def test_manifest_rejects_old_installer(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _installers(artifacts)
    private, public, _key = _release_key(tmp_path / "keys")
    (artifacts / "PartyOps_1.4.3-rc.4_windows_amd64.exe").write_bytes(b"old")
    with pytest.raises(ValueError, match="旧版或未知安装包"):
        MODULE.build_manifest(
            root=artifacts,
            output=artifacts / "manifest.json",
            source_commit="a" * 40,
            tooling_commit="b" * 40,
            macos_source_commit="c" * 40,
            macos_workflow_commit="d" * 40,
            macos_build_run="32681880115",
            generated_at="2026-08-18T10:00:00+08:00",
            private_key_path=private,
            public_key_path=public,
        )


def test_manifest_rejects_mismatched_key_and_unverified_target(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    _installers(artifacts)
    private, _public, _key = _release_key(tmp_path / "first")
    _other_private, other_public, _other_key = _release_key(tmp_path / "second")
    with pytest.raises(ValueError, match="不匹配"):
        MODULE.build_manifest(
            root=artifacts,
            output=artifacts / "manifest.json",
            source_commit="a" * 40,
            tooling_commit="b" * 40,
            macos_source_commit="c" * 40,
            macos_workflow_commit="d" * 40,
            macos_build_run="1",
            generated_at="2026-08-24T16:00:00+08:00",
            private_key_path=private,
            public_key_path=other_public,
        )
    _private, public, _key = _release_key(tmp_path / "third")
    with pytest.raises(ValueError, match="当前发布目标"):
        MODULE.build_manifest(
            root=artifacts,
            output=artifacts / "manifest.json",
            source_commit="a" * 40,
            tooling_commit="b" * 40,
            macos_source_commit="c" * 40,
            macos_workflow_commit="d" * 40,
            macos_build_run="1",
            generated_at="2026-08-24T16:00:00+08:00",
            private_key_path=_private,
            public_key_path=public,
            native_verified_platforms=("linux/loongarch64",),
        )
    leaked_private, leaked_public, _leaked_key = _release_key(artifacts / "leaked-keys")
    with pytest.raises(ValueError, match="禁止放入公开制品目录"):
        MODULE.build_manifest(
            root=artifacts,
            output=artifacts / "manifest.json",
            source_commit="a" * 40,
            tooling_commit="b" * 40,
            macos_source_commit="c" * 40,
            macos_workflow_commit="d" * 40,
            macos_build_run="1",
            generated_at="2026-08-24T16:00:00+08:00",
            private_key_path=leaked_private,
            public_key_path=leaked_public,
        )


@pytest.mark.parametrize(
    ("platform", "architecture", "runtime_profile"),
    [
        ("windows", "amd64", "full"),
        ("windows7", "amd64", "legacy-full"),
        ("windows7", "x86", "legacy-core"),
    ],
)
def test_embedded_windows_manifest_preserves_target_identity(
    tmp_path: Path,
    platform: str,
    architecture: str,
    runtime_profile: str,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "PartyOps.exe").write_bytes(b"MZ")
    output = bundle / "release-manifest.json"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate-release-manifest.py"),
            "--root",
            str(bundle),
            "--output",
            str(output),
            "--version",
            "1.4.3-rc.8",
            "--tag",
            "v1.4.3-rc.8",
            "--commit",
            "a" * 40,
            "--platform",
            platform,
            "--architecture",
            architecture,
            "--runtime-profile",
            runtime_profile,
        ],
        check=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["platform"] == platform
    assert manifest["architecture"] == architecture
    assert manifest["runtime_profile"] == runtime_profile
