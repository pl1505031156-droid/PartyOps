"""为最终公开发布目录生成可审计的多平台制品清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


INSTALLERS = {
    "PartyOps_1.4.3-rc.5_windows_amd64.exe": "windows/amd64",
    "PartyOps_1.4.3-rc.5_windows7_amd64.exe": "windows7/amd64",
    "PartyOps_1.4.3-rc.5_windows7_x86.exe": "windows7/x86",
    "PartyOps_1.4.3-rc.5_linux_amd64.deb": "linux-deb/amd64",
    "PartyOps_1.4.3-rc.5_linux_arm64.deb": "linux-deb/arm64",
    "PartyOps-1.4.3-0.rc.5.1.x86_64.rpm": "linux-rpm/amd64",
    "PartyOps-1.4.3-0.rc.5.1.aarch64.rpm": "linux-rpm/arm64",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(
    *,
    root: Path,
    output: Path,
    source_commit: str,
    tooling_commit: str,
    generated_at: str,
) -> dict[str, object]:
    timestamp = datetime.fromisoformat(generated_at)
    if timestamp.tzinfo is None:
        raise ValueError("清单生成时间必须包含时区")
    missing = sorted(name for name in INSTALLERS if not (root / name).is_file())
    if missing:
        raise FileNotFoundError(f"缺少七平台安装包：{', '.join(missing)}")
    stale = sorted(
        path.name
        for path in root.iterdir()
        if path.is_file()
        and path.suffix.lower() in {".exe", ".deb", ".rpm"}
        and path.name not in INSTALLERS
    )
    if stale:
        raise ValueError(f"发布目录含旧版或未知安装包：{', '.join(stale)}")

    assets = []
    for path in sorted(item for item in root.iterdir() if item.is_file()):
        if path.resolve() == output.resolve():
            continue
        assets.append(
            {"filename": path.name, "size": path.stat().st_size, "sha256": sha256(path)}
        )
    return {
        "schema_version": 4,
        "product": "PartyOps",
        "version": "1.4.3-rc.5",
        "release_tag": "v1.4.3-rc.5",
        "source_commit": source_commit,
        "release_tooling_commit": tooling_commit,
        "generated_at": generated_at,
        "timezone": "Asia/Shanghai (UTC+8)",
        "release_type": "ordinary",
        "prerelease": False,
        "make_latest": False,
        "signed": False,
        "verified_platforms": list(INSTALLERS.values()),
        "native_machine_validation": False,
        "limitations": [
            "Windows 7 与国产 Linux 尚未在对应真机运行验收",
            "Windows 安装器尚无商业代码签名",
            "官网与 CloudStudio 只挂载当前版本安装包",
        ],
        "assets": assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--tooling-commit", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    payload = build_manifest(
        root=root,
        output=output,
        source_commit=args.source_commit,
        tooling_commit=args.tooling_commit,
        generated_at=args.generated_at,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".incoming",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"最终发布清单已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
