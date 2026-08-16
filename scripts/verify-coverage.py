"""分别校验 coverage.py JSON 中的行与分支覆盖率。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PartyOps 后端覆盖率发布门禁")
    parser.add_argument("report", type=Path)
    parser.add_argument("--line", type=float, default=90.0)
    parser.add_argument("--branch", type=float, default=90.0)
    args = parser.parse_args()

    payload = json.loads(args.report.read_text(encoding="utf-8"))
    totals = payload.get("totals", {})
    line_percent = float(totals.get("percent_statements_covered", 0.0))
    branch_percent = float(totals.get("percent_branches_covered", 0.0))
    print(
        f"后端覆盖率：行 {line_percent:.2f}%（门禁 {args.line:.2f}%），"
        f"分支 {branch_percent:.2f}%（门禁 {args.branch:.2f}%）"
    )
    failures: list[str] = []
    if line_percent < args.line:
        failures.append("行覆盖率未达到正式发布门禁")
    if branch_percent < args.branch:
        failures.append("分支覆盖率未达到正式发布门禁")
    if failures:
        for message in failures:
            print(f"失败：{message}")
        return 1
    print("覆盖率门禁通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
