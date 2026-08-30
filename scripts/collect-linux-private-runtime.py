#!/usr/bin/env python3
"""为 Linux LibreOffice 收集私有 glibc 的确定性 ELF 依赖闭包。"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path


NEEDED_RE = re.compile(r"Shared library: \[(?P<name>[^]]+)]")


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def _needed(path: Path) -> tuple[str, ...]:
    result = subprocess.run(
        ["readelf", "-d", str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return ()
    return tuple(match.group("name") for match in NEEDED_RE.finditer(result.stdout))


def _resolve_from_tree(path: Path, tree_root: Path | None) -> Path | None:
    """解析 sysroot 内的绝对符号链接，避免错误落到构建宿主根目录。"""

    if tree_root is None:
        resolved = path.resolve(strict=False)
        return resolved if resolved.is_file() else None
    current = path
    for _ in range(32):
        if not current.is_symlink():
            return current if current.is_file() else None
        target = Path(os.readlink(current))
        current = (
            tree_root / str(target).lstrip("/")
            if target.is_absolute()
            else current.parent / target
        )
        current = Path(os.path.normpath(current))
        try:
            current.relative_to(tree_root)
        except ValueError:
            return None
    return None


def _index_names(roots: list[Path], tree_root: Path | None = None) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not (path.is_file() or path.is_symlink()):
                continue
            resolved = _resolve_from_tree(path, tree_root)
            if resolved is not None and _is_elf(resolved):
                index.setdefault(path.name, resolved)
    return index


def _find_named_file(
    roots: list[Path], name: str, tree_root: Path | None = None
) -> Path | None:
    """按固定根目录顺序解析需随包复制的动态模块或校验文件。"""

    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(name)):
            resolved = _resolve_from_tree(path, tree_root)
            if resolved is not None:
                return resolved
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--sysroot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--loader", required=True)
    parser.add_argument("--exclude-name", action="append", default=[])
    parser.add_argument(
        "--seed-name",
        action="append",
        default=[],
        help="必须复制并纳入 ELF 依赖闭包的 dlopen 动态模块文件名",
    )
    parser.add_argument(
        "--copy-name",
        action="append",
        default=[],
        help="必须复制但无需解析 ELF 依赖的伴随文件名",
    )
    args = parser.parse_args()

    runtime = args.runtime.resolve()
    sysroot = args.sysroot.resolve()
    output = args.output.resolve()
    if not (runtime / "program" / "soffice.bin").is_file():
        raise SystemExit(f"LibreOffice 运行时不完整：{runtime}")
    if output.exists():
        raise SystemExit(f"输出目录已存在，拒绝覆盖：{output}")
    output.mkdir(parents=True)

    excluded = set(args.exclude_name)
    runtime_paths = [
        path
        for path in runtime.rglob("*")
        if path.is_file() and path.name not in excluded and _is_elf(path)
    ]
    runtime_index = _index_names([runtime])
    for name in excluded:
        runtime_index.pop(name, None)
    system_roots = [
        sysroot / "usr/lib64",
        sysroot / "usr/lib",
        sysroot / "lib64",
        sysroot / "lib",
    ]
    system_index = _index_names(system_roots, tree_root=sysroot)

    queue = list(runtime_paths)
    visited: set[Path] = set()
    copied: dict[str, Path] = {}
    unresolved: dict[str, list[str]] = {}
    for name in args.seed_name:
        source = system_index.get(name)
        if source is None:
            raise SystemExit(f"未找到必须随包携带的动态模块：{name}")
        shutil.copy2(source, output / name)
        copied[name] = source
        queue.append(source)
    while queue:
        current = queue.pop()
        resolved_current = current.resolve()
        if resolved_current in visited:
            continue
        visited.add(resolved_current)
        for name in _needed(resolved_current):
            if name in runtime_index:
                queue.append(runtime_index[name])
                continue
            source = system_index.get(name)
            if source is None:
                unresolved.setdefault(name, []).append(str(resolved_current))
                continue
            if name not in copied:
                shutil.copy2(source, output / name)
                copied[name] = source
                queue.append(source)

    loader_source = system_index.get(args.loader)
    if loader_source is None:
        raise SystemExit(f"未找到私有 ELF 加载器：{args.loader}")
    shutil.copy2(loader_source, output / args.loader)
    copied[args.loader] = loader_source

    for name in args.copy_name:
        source = _find_named_file(system_roots, name, tree_root=sysroot)
        if source is None:
            raise SystemExit(f"未找到必须随包携带的伴随文件：{name}")
        shutil.copy2(source, output / name)
        copied[name] = source

    if unresolved:
        for name, parents in sorted(unresolved.items()):
            print(f"UNRESOLVED {name}")
            for parent in sorted(set(parents))[:5]:
                print(f"  {parent}")
        return 2

    manifest = output.parent / "PRIVATE_RUNTIME_LIBS.txt"
    manifest.write_text(
        "".join(
            f"{name}\t{source.relative_to(sysroot)}\t{_sha256(output / name)}\n"
            for name, source in sorted(copied.items())
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"LIBRARIES={len(copied)}")
    print(f"BYTES={sum(path.stat().st_size for path in output.iterdir() if path.is_file())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
