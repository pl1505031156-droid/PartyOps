"""rc.4 macOS 原生发布门禁的兼容性回归测试。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build-macos-1.4.5-rc.4.yml"


def test_macos_upgrade_backup_gate_supports_system_bash_32() -> None:
    """Apple 系统 Bash 3.2 不提供 mapfile，发布门禁不得依赖它。"""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "\n          mapfile -t" not in workflow
    assert 'upgrade_backup="$("$upgrade_env/bin/python" -c' in workflow
    assert "len(paths) != 1" in workflow
    assert '"$PWD" "$upgrade_backup"' in workflow
