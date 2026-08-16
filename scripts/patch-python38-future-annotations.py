"""为经审核的纯 Python 依赖回移延迟注解语义。"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


FUTURE_LINE = "from __future__ import annotations\n"


def patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if FUTURE_LINE.strip() in text:
        return False
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    insertion = 0
    if lines and lines[0].startswith("#!"):
        insertion = 1
    if insertion < len(lines) and "coding" in lines[insertion][:80]:
        insertion += 1
    if tree.body:
        first = tree.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            insertion = max(insertion, int(first.end_lineno or first.lineno))
    lines.insert(insertion, FUTURE_LINE)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="为 Python 3.8 纯 Python 安全依赖补齐 PEP 563 延迟注解"
    )
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args()
    changed = 0
    for root in args.roots:
        if not root.is_dir():
            raise SystemExit(f"依赖源码目录不存在：{root}")
        for path in sorted(root.rglob("*.py")):
            changed += int(patch_file(path))
    print(f"已为 {changed} 个 Python 模块补齐延迟注解语义。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
