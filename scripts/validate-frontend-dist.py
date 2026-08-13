"""验证 Vite 生产目录的入口与静态资源闭包，防止安装包出现空白页。"""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit


REFERENCE_PATTERNS = (
    re.compile(r'''(?:src|href)=["']([^"'#?]+)'''),
    re.compile(r'''(?:import\s*\(|from\s*)["']([^"']+)["']'''),
    re.compile(r'''url\(["']?([^"')?#]+)'''),
)


def _local_reference(current: Path, root: Path, raw: str) -> Path | None:
    # 生产 JS 中可能保留运行时模板（例如 `assets/${name}`）。它不是一个
    # 可在打包阶段静态求值的文件名，不能误报成字面量缺失资源。
    if "${" in raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or raw.startswith(("data:", "#")):
        return None
    if raw.startswith("/"):
        candidate = root / raw.lstrip("/")
    else:
        candidate = current.parent / raw
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"静态资源引用越出生产目录：{current} -> {raw}")
    return resolved


def validate_frontend_dist(root: Path) -> dict[str, object]:
    root = root.resolve()
    index = root / "index.html"
    if not index.is_file() or index.stat().st_size == 0:
        raise ValueError(f"缺少非空前端入口：{index}")
    queue: deque[Path] = deque([index])
    visited: set[Path] = set()
    missing: list[str] = []
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        if not current.is_file() or current.stat().st_size == 0:
            missing.append(str(current.relative_to(root)))
            continue
        if current.suffix.lower() not in {".html", ".js", ".mjs", ".css"}:
            continue
        text = current.read_text(encoding="utf-8", errors="replace")
        for pattern in REFERENCE_PATTERNS:
            for raw in pattern.findall(text):
                candidate = _local_reference(current, root, raw)
                if candidate is not None and candidate not in visited:
                    queue.append(candidate)
    if missing:
        raise ValueError("前端静态资源闭包不完整：" + ", ".join(sorted(missing)[:20]))
    asset_files = [path for path in root.rglob("*") if path.is_file()]
    if len(asset_files) < 10:
        raise ValueError(f"前端生产目录只有 {len(asset_files)} 个文件，疑似构建不完整")
    return {
        "root": str(root),
        "entry": "index.html",
        "referenced_files": len(visited),
        "total_files": len(asset_files),
        "status": "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_frontend_dist(args.root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
