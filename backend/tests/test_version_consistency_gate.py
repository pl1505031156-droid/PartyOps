from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-version-consistency.py"
SPEC = importlib.util.spec_from_file_location("partyops_version_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


VERSION_INPUTS = (
    "backend/app/__init__.py",
    "backend/app/client_agent.py",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "frontend/package.json",
    "packaging/windows/PartyOps.iss",
    "packaging/windows/build-windows.ps1",
    "packaging/windows/build-windows7.ps1",
    "packaging/uos/build-update-package.sh",
    "scripts/generate-update-catalog.py",
    "packaging/linux/build-native.sh",
    "packaging/uos/build-portable.sh",
    "packaging/linux/post-install-selftest.sh",
    "scripts/build-platform-update-packages.py",
    "scripts/generate-release-bundle-manifest.py",
)


def _copy_version_inputs(target_root: Path) -> None:
    for relative in VERSION_INPUTS:
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())


def test_repository_versions_are_consistent() -> None:
    MODULE.verify(ROOT, "1.4.5-rc.2")


def test_independent_website_is_not_required_by_application_freeze(
    tmp_path: Path,
) -> None:
    _copy_version_inputs(tmp_path)

    MODULE.verify(tmp_path, "1.4.5-rc.2")


def test_python_metadata_mismatch_is_rejected(tmp_path: Path) -> None:
    _copy_version_inputs(tmp_path)
    (tmp_path / "backend/pyproject.toml").write_text(
        '[project]\nversion = "1.4.3rc4"\n', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Python 项目元数据版本不一致"):
        MODULE.verify(tmp_path, "1.4.5-rc.2")


def test_linux_installed_runtime_version_mismatch_is_rejected(tmp_path: Path) -> None:
    _copy_version_inputs(tmp_path)
    selftest = tmp_path / "packaging/linux/post-install-selftest.sh"
    selftest.write_text(
        selftest.read_text(encoding="utf-8").replace(
            'EXPECTED_VERSION="1.4.5-rc.2"',
            'EXPECTED_VERSION="1.4.2"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Linux 安装后自检版本不一致"):
        MODULE.verify(tmp_path, "1.4.5-rc.2")
