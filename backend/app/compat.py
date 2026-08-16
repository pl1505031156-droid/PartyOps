"""主线与 Windows 7 Python 3.8 共用的集中兼容层。"""

from __future__ import annotations

import asyncio
import builtins
import contextvars
import functools
import sys
from collections.abc import Callable, Iterable, Iterator
from enum import Enum
from typing import Any

try:
    from enum import StrEnum as StrEnum
except ImportError:  # pragma: no cover - 仅 Python 3.8 Legacy 运行时进入。
    class StrEnum(str, Enum):
        """与 Python 3.11 enum.StrEnum 保持业务所需的字符串语义。"""

        def __str__(self) -> str:
            return str(self.value)


def install_legacy_typing_aliases() -> None:
    """为 Python 3.8 安装安全新版依赖所需的标准 typing 名称。

    FastAPI/Starlette 的安全修复版本仍是纯 Python，但其最低版本声明已提高。
    这些名称都由 ``typing_extensions`` 官方兼容包提供；只在旧解释器缺失时
    映射，不替换主线 Python 自带实现。
    """

    if sys.version_info >= (3, 10):
        return
    import typing
    import typing_extensions

    for name in (
        "Annotated",
        "LiteralString",
        "NotRequired",
        "ParamSpec",
        "Required",
        "Self",
        "TypeAlias",
        "TypeGuard",
        "TypeVarTuple",
        "Unpack",
    ):
        if not hasattr(typing, name):
            setattr(typing, name, getattr(typing_extensions, name))


async def to_thread(function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """兼容 Python 3.8 的 asyncio.to_thread，并传播 contextvars。"""

    native = getattr(asyncio, "to_thread", None)
    if native is not None:
        return await native(function, *args, **kwargs)
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    call = functools.partial(context.run, function, *args, **kwargs)
    return await loop.run_in_executor(None, call)


def strict_zip(*iterables: Iterable[Any]) -> Iterator[tuple[Any, ...]]:
    """兼容 Python 3.8 的 zip(strict=True)，长度不一致时明确失败。"""

    if sys.version_info >= (3, 10):
        yield from builtins.zip(*iterables, strict=True)
        return
    sentinel = object()
    iterators = [iter(value) for value in iterables]
    while True:
        values = tuple(next(iterator, sentinel) for iterator in iterators)
        ended = tuple(value is sentinel for value in values)
        if all(ended):
            return
        if any(ended):
            raise ValueError("strict_zip 的输入长度不一致")
        yield values
