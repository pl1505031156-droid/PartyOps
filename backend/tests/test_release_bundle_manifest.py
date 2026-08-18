from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "generate-release-bundle-manifest.py"
SPEC = importlib.util.spec_from_file_location("release_bundle_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _installers(root: Path) -> None:
    for name in MODULE.INSTALLERS:
        (root / name).write_bytes(name.encode("utf-8"))


def test_manifest_requires_only_current_seven_installers(tmp_path: Path) -> None:
    _installers(tmp_path)
    output = tmp_path / "PartyOps_1.4.3-rc.6_release-manifest.json"
    payload = MODULE.build_manifest(
        root=tmp_path,
        output=output,
        source_commit="a" * 40,
        tooling_commit="b" * 40,
        generated_at="2026-08-18T10:00:00+08:00",
    )
    assert payload["schema_version"] == 4
    assert payload["prerelease"] is False
    assert payload["make_latest"] is False
    assert payload["verified_platforms"] == list(MODULE.INSTALLERS.values())
    assert {asset["filename"] for asset in payload["assets"]} == set(MODULE.INSTALLERS)


def test_manifest_rejects_old_installer(tmp_path: Path) -> None:
    _installers(tmp_path)
    (tmp_path / "PartyOps_1.4.3-rc.4_windows_amd64.exe").write_bytes(b"old")
    with pytest.raises(ValueError, match="旧版或未知安装包"):
        MODULE.build_manifest(
            root=tmp_path,
            output=tmp_path / "manifest.json",
            source_commit="a" * 40,
            tooling_commit="b" * 40,
            generated_at="2026-08-18T10:00:00+08:00",
        )
