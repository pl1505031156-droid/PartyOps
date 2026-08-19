"""生成嵌入 Windows 安装器的可审计组件清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--runtime-profile", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.resolve() == output:
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    payload = {
        "schema_version": 1,
        "product": "PartyOps",
        "version": args.version,
        "release_tag": args.tag,
        "source_commit": args.commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": args.platform,
        "architecture": args.architecture,
        "runtime_profile": args.runtime_profile,
        "signed": False,
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
