"""验证 Legacy 纯 Python 依赖回移工具不会破坏模块结构。"""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "patch-python38-future-annotations.py"
SPEC = importlib.util.spec_from_file_location("partyops_patch_py38_annotations", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_patch_preserves_shebang_encoding_and_docstring(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text(
        '#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n"""说明。"""\nVALUE: list[str] | None = None\n',
        encoding="utf-8",
    )
    assert MODULE.patch_file(source) is True
    text = source.read_text(encoding="utf-8")
    assert text.splitlines()[3] == "from __future__ import annotations"
    compile(text, str(source), "exec")
    assert MODULE.patch_file(source) is False
