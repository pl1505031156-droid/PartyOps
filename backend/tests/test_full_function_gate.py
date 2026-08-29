"""全功能测试与逐平台打包顺序门禁。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "verify-full-function-gate.py"


def _run(root: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), mode, "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_gate_requires_record_and_invalidates_after_source_change(tmp_path: Path) -> None:
    assert _run(tmp_path, "verify").returncode == 2

    source = tmp_path / "backend" / "app" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    assert _run(tmp_path, "record").returncode == 0
    assert _run(tmp_path, "verify").returncode == 0

    payload = json.loads(
        (tmp_path / ".release-gates" / "full-function-tests.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "passed"
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["tested_at"].endswith("+08:00")

    source.write_text("VALUE = 2\n", encoding="utf-8")
    stale = _run(tmp_path, "verify")
    assert stale.returncode == 2
    assert "FULL_FUNCTION_GATE_STALE" in stale.stderr


def test_every_platform_builder_verifies_full_function_gate() -> None:
    windows = (ROOT / "packaging" / "windows" / "build-windows.ps1").read_text(encoding="utf-8")
    linux = (ROOT / "packaging" / "linux" / "build-native.sh").read_text(encoding="utf-8")
    macos = (ROOT / "packaging" / "macos" / "build-pkg.sh").read_text(encoding="utf-8")
    release_tests = (ROOT / "scripts" / "test.ps1").read_text(encoding="utf-8")

    assert "verify-full-function-gate.py" in windows and " verify --root " in windows
    assert "verify-full-function-gate.py" in linux and " verify --root " in linux
    assert "verify-full-function-gate.py" in macos and " verify --root " in macos
    assert "verify-full-function-gate.py" in release_tests and " record --root " in release_tests
    assert "external" not in windows.lower()
    assert "PARTYOPS_OFFICE_RUNTIME_MODE" not in linux
