"""按 UOS/Linux 环境标记验证离线 wheelhouse 的依赖闭包。"""

from __future__ import annotations

import argparse
import email
import sys
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


@dataclass(frozen=True)
class WheelMetadata:
    name: str
    version: Version
    requires_dist: tuple[str, ...]
    path: Path


def linux_environment(architecture: str = "amd64") -> dict[str, str]:
    """返回套件目标机 CPython 3.11 / Linux 的标记环境。"""

    environment = default_environment()
    environment.update(
        {
            "implementation_name": "cpython",
            "implementation_version": "3.11.15",
            "os_name": "posix",
            "platform_machine": "aarch64" if architecture == "arm64" else "x86_64",
            "platform_python_implementation": "CPython",
            "platform_system": "Linux",
            "python_full_version": "3.11.15",
            "python_version": "3.11",
            "sys_platform": "linux",
        }
    )
    return environment


def read_wheels(wheelhouse: Path) -> dict[str, WheelMetadata]:
    wheels: dict[str, WheelMetadata] = {}
    for path in sorted(wheelhouse.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            if len(metadata_names) != 1:
                raise ValueError(f"{path.name} 的 METADATA 数量异常")
            message = email.message_from_bytes(archive.read(metadata_names[0]))
        raw_name = message.get("Name")
        raw_version = message.get("Version")
        if not raw_name or not raw_version:
            raise ValueError(f"{path.name} 缺少 Name 或 Version")
        name = canonicalize_name(raw_name)
        if name in wheels:
            raise ValueError(f"离线目录包含重复包：{raw_name}")
        wheels[name] = WheelMetadata(
            name=name,
            version=Version(raw_version),
            requires_dist=tuple(message.get_all("Requires-Dist", [])),
            path=path,
        )
    return wheels


def read_roots(paths: list[Path]) -> list[Requirement]:
    roots: list[Requirement] = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            value = line.split("#", 1)[0].strip()
            if not value:
                continue
            try:
                roots.append(Requirement(value))
            except ValueError as exc:
                raise ValueError(f"{path}:{number} 依赖格式错误：{value}") from exc
    return roots


def marker_applies(
    requirement: Requirement, extras: set[str], architecture: str
) -> bool:
    if requirement.marker is None:
        return True
    environment = linux_environment(architecture)
    for extra in extras or {""}:
        environment["extra"] = extra
        if requirement.marker.evaluate(environment):
            return True
    return False


def validate(
    wheelhouse: Path, requirement_files: list[Path], architecture: str = "amd64"
) -> int:
    wheels = read_wheels(wheelhouse)
    roots = read_roots(requirement_files)
    requested_extras: dict[str, set[str]] = defaultdict(set)
    constraints: dict[str, list[Requirement]] = defaultdict(list)
    queue: deque[str] = deque()
    queued: set[str] = set()
    processed_extras: dict[str, frozenset[str]] = {}

    def require(requirement: Requirement, parent_extras: set[str]) -> None:
        if not marker_applies(requirement, parent_extras, architecture):
            return
        name = canonicalize_name(requirement.name)
        constraints[name].append(requirement)
        before = frozenset(requested_extras[name])
        requested_extras[name].update(requirement.extras)
        if name not in queued or frozenset(requested_extras[name]) != before:
            queue.append(name)
            queued.add(name)

    for root in roots:
        require(root, {""})

    missing: list[str] = []
    incompatible: list[str] = []
    while queue:
        name = queue.popleft()
        extras = frozenset(requested_extras[name])
        if processed_extras.get(name) == extras:
            continue
        processed_extras[name] = extras
        wheel = wheels.get(name)
        if wheel is None:
            requested_by = ", ".join(str(item) for item in constraints[name])
            missing.append(f"{name}（要求：{requested_by}）")
            continue
        for requirement in constraints[name]:
            if requirement.specifier and wheel.version not in requirement.specifier:
                incompatible.append(
                    f"{wheel.path.name} 不满足 {requirement.specifier}"
                )
        for raw_dependency in wheel.requires_dist:
            require(Requirement(raw_dependency), set(extras))

    if missing or incompatible:
        if missing:
            print("UOS/Linux 离线依赖缺失：", file=sys.stderr)
            for item in missing:
                print(f"  - {item}", file=sys.stderr)
        if incompatible:
            print("UOS/Linux 离线依赖版本不兼容：", file=sys.stderr)
            for item in incompatible:
                print(f"  - {item}", file=sys.stderr)
        return 2

    print(
        f"UOS/Linux {architecture} 离线依赖闭包验证通过："
        f"{len(processed_extras)} 个运行/构建包，wheelhouse 共 {len(wheels)} 个文件。"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, nargs="+", required=True)
    parser.add_argument(
        "--architecture", choices=("amd64", "arm64"), default="amd64"
    )
    args = parser.parse_args()
    return validate(
        args.wheelhouse.resolve(),
        [path.resolve() for path in args.requirements],
        args.architecture,
    )


if __name__ == "__main__":
    raise SystemExit(main())
