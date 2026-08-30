"""rc.6 macOS 原生发布门禁的兼容性回归测试。"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build-macos-1.4.5-rc.6.yml"


def test_macos_upgrade_backup_gate_supports_system_bash_32() -> None:
    """Apple 系统 Bash 3.2 不提供 mapfile，发布门禁不得依赖它。"""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "\n          mapfile -t" not in workflow
    assert 'upgrade_backup="$("$upgrade_env/bin/python" -c' in workflow
    assert "assert len(paths) == 1" in workflow
    assert '"$PWD" "$upgrade_backup"' in workflow


def test_macos_upgrade_backup_python_gate_accepts_exactly_one_file(
    tmp_path: Path,
) -> None:
    """工作流内联门禁必须在 1 个文件时成功，在 0/2 个文件时拒绝。"""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    line = next(line for line in workflow.splitlines() if "paths=sorted" in line)
    code = line.strip().removeprefix("'").removesuffix("' \\")

    missing = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0

    expected = tmp_path / "PartyOps-pre-upgrade-one.partyops-backup"
    expected.touch()
    exact = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert exact.returncode == 0
    assert exact.stdout.strip() == str(expected)

    (tmp_path / "PartyOps-pre-upgrade-two.partyops-backup").touch()
    duplicate = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode != 0
