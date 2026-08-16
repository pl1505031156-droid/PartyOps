"""流式校验 Linux 便携载荷 TAR；从标准输入读取未压缩 TAR 数据。"""

from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import PurePosixPath


def validate(
    *,
    expected_root: str,
    max_members: int,
    max_bytes: int,
    allow_implicit_root: bool = False,
) -> None:
    seen: set[str] = set()
    expanded = 0
    count = 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        for member in archive:
            count += 1
            if count > max_members:
                raise ValueError("PORTABLE_TAR_MEMBER_LIMIT：载荷成员数量超过上限")
            name = member.name
            parts = PurePosixPath(name).parts
            if (
                not name
                or len(name) > 4096
                or name.startswith("/")
                or "//" in name
                or "\\" in name
                or not parts
                or parts[0] != expected_root
                or any(
                    part in {"", ".", ".."}
                    or ":" in part
                    or part.endswith((" ", "."))
                    or any(ord(character) < 32 for character in part)
                    for part in parts
                )
            ):
                raise ValueError(f"PORTABLE_TAR_PATH_INVALID：{name!r}")
            collision_key = "/".join(parts).casefold()
            if collision_key in seen:
                raise ValueError(f"PORTABLE_TAR_DUPLICATE：{name!r}")
            seen.add(collision_key)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"PORTABLE_TAR_SPECIAL_FILE：{name!r}")
            if member.mode & 0o6002:
                raise ValueError(f"PORTABLE_TAR_MODE_INVALID：{name!r}")
            expanded += max(0, int(member.size))
            if expanded > max_bytes:
                raise ValueError("PORTABLE_TAR_EXPANDED_LIMIT：载荷展开体积超过上限")
    if not seen:
        raise ValueError("PORTABLE_TAR_EMPTY：载荷为空")
    if not allow_implicit_root and expected_root.casefold() not in seen:
        raise ValueError("PORTABLE_TAR_ROOT_MISSING：载荷缺少唯一顶层目录")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PartyOps Linux 便携 TAR")
    parser.add_argument("--expected-root", default="PartyOps")
    parser.add_argument("--max-members", type=int, default=200_000)
    parser.add_argument("--max-bytes", type=int, default=20 * 1024**3)
    parser.add_argument(
        "--allow-implicit-root",
        action="store_true",
        help="允许上游源码归档省略顶层目录成员，但所有文件仍必须位于该根下",
    )
    args = parser.parse_args()
    try:
        validate(
            expected_root=args.expected_root,
            max_members=args.max_members,
            max_bytes=args.max_bytes,
            allow_implicit_root=args.allow_implicit_root,
        )
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("Linux 便携载荷 TAR 路径、类型、权限和展开体积校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
