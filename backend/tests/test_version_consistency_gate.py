from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-version-consistency.py"
SPEC = importlib.util.spec_from_file_location("partyops_version_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_repository_versions_are_consistent() -> None:
    MODULE.verify(ROOT, "1.4.3-rc.6")


def test_python_metadata_mismatch_is_rejected(tmp_path: Path) -> None:
    files = {
        "backend/app/__init__.py": '__version__ = "1.4.3-rc.6"\n',
        "backend/app/client_agent.py": 'AGENT_VERSION = "1.4.3-rc.6"\n',
        "backend/pyproject.toml": '[project]\nversion = "1.4.3rc4"\n',
        "backend/uv.lock": '[[package]]\nname = "partyops"\nversion = "1.4.3rc6"\n',
    }
    for relative, content in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for relative in ("frontend/package.json", "website/package.json"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"version": "1.4.3-rc.6"}), encoding="utf-8")

    with pytest.raises(ValueError, match="Python 项目元数据版本不一致"):
        MODULE.verify(tmp_path, "1.4.3-rc.6")
