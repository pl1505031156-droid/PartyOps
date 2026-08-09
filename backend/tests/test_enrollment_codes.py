"""设备入网码规范化的安全与国产浏览器兼容测试。"""

from __future__ import annotations

import pytest

from app.enrollment_codes import normalize_enrollment_code


def enrollment_code() -> str:
    return f"{'Ab_9-xYz0123456789QwErTy'[:24]}.{'A1' * 32}"


@pytest.mark.parametrize(
    "decorate",
    [
        lambda code: code,
        lambda code: f"  {code[:31]}\n{code[31:]}  ",
        lambda code: f"一次性入网码（89 个字符）：\u200b{code}\ufeff",
        lambda code: f"入网码：{code.replace('.', '．')}；请在十分钟内使用",
    ],
)
def test_normalize_enrollment_code_accepts_common_clipboard_forms(decorate) -> None:
    code = enrollment_code()
    assert normalize_enrollment_code(decorate(code)) == code.rsplit(".", 1)[0] + "." + ("a1" * 32)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "只是提示文字，没有入网码",
        enrollment_code()[:-1],
        "x" * 1025,
        f"{enrollment_code()} 另一条 {'Z' * 24}.{'b2' * 32}",
    ],
)
def test_normalize_enrollment_code_rejects_empty_truncated_oversized_or_ambiguous(
    value: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_enrollment_code(value)
