"""电子表格导出的公式注入防护。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def safe_spreadsheet_cell(value: Any) -> Any:
    """把不可信文本固定为单元格文字，避免 Excel/CSV 将其作为公式执行。"""

    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" ")
    if candidate.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_spreadsheet_row(values: Iterable[Any]) -> list[Any]:
    return [safe_spreadsheet_cell(value) for value in values]
