"""PartyOps 发布版本的统一解析与比较。"""

from __future__ import annotations

import re

from packaging.version import InvalidVersion, Version

from .problems import ProblemException


def parse_release_version(value: object) -> Version:
    """解析 PEP 440 版本，并接受官网使用的 ``1.4.3-rc.3`` 写法。"""

    raw = str(value or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-?rc\.?\d+)?", raw, flags=re.IGNORECASE):
        raise ProblemException(
            422,
            "UPDATE_VERSION_INVALID",
            "更新版本号无效",
            "更新包版本必须使用标准版本格式，例如 1.4.3-rc.3。",
        )
    try:
        parsed = Version(raw)
    except InvalidVersion as exc:
        raise ProblemException(
            422,
            "UPDATE_VERSION_INVALID",
            "更新版本号无效",
            "更新包版本必须使用标准版本格式，例如 1.4.3-rc.3。",
        ) from exc
    if parsed.epoch != 0 or parsed.local is not None or len(parsed.release) != 3:
        raise ProblemException(
            422,
            "UPDATE_VERSION_INVALID",
            "更新版本号无效",
            "更新包版本只能包含三段版本号和可选候选标识，不得包含 epoch 或本地版本段。",
        )
    return parsed
