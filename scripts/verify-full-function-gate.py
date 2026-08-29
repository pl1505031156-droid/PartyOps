#!/usr/bin/env python3
"""记录并校验“全功能测试通过后才允许打包”的发布门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


GATE_RELATIVE_PATH = Path(".release-gates/full-function-tests.json")
BEIJING = timezone(timedelta(hours=8))
INCLUDE_ROOTS = (
    "backend/app",
    "backend/tests",
    "frontend/src",
    "frontend/tests",
    "website/src",
    "website/edge-functions",
    "website/tests",
    "packaging",
    "scripts",
)
INCLUDE_FILES = (
    "backend/pyproject.toml",
    "backend/requirements.txt",
    "backend/requirements-release.txt",
    "backend/uv.lock",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/vite.config.ts",
    "website/package.json",
    "website/pnpm-lock.yaml",
    "website/vite.config.js",
)
EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    "coverage",
    "htmlcov",
    "dist",
    "artifacts",
}


def _candidate_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for relative in INCLUDE_ROOTS:
        base = root / relative
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts):
                files.add(path)
    for relative in INCLUDE_FILES:
        path = root / relative
        if path.is_file():
            files.add(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def source_fingerprint(root: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = _candidate_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest(), len(files)


def record(root: Path) -> int:
    fingerprint, file_count = source_fingerprint(root)
    target = root / GATE_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "status": "passed",
        "tested_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "source_fingerprint": fingerprint,
        "source_file_count": file_count,
        "suite": "scripts/test.ps1",
        "scope": [
            "document-formatter-source-release-x64-x86-build-and-regression",
            "backend-full-pytest-and-coverage",
            "frontend-typecheck-tests-coverage-build",
            "website-tests-coverage-build",
            "dependency-audits-secret-scan-static-analysis",
        ],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[FULL_FUNCTION_GATE_RECORDED] {target} {fingerprint}")
    return 0


def verify(root: Path) -> int:
    target = root / GATE_RELATIVE_PATH
    if not target.is_file():
        print(
            "[FULL_FUNCTION_GATE_MISSING] 尚未完成全功能测试；请先运行 scripts/test.ps1，拒绝生成平台安装包。",
            file=sys.stderr,
        )
        return 2
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[FULL_FUNCTION_GATE_INVALID] 门禁记录不可读：{exc}", file=sys.stderr)
        return 2
    fingerprint, file_count = source_fingerprint(root)
    if payload.get("status") != "passed" or payload.get("source_fingerprint") != fingerprint:
        print(
            "[FULL_FUNCTION_GATE_STALE] 测试后源码已变化；必须重新运行 scripts/test.ps1，拒绝生成平台安装包。",
            file=sys.stderr,
        )
        return 2
    if payload.get("source_file_count") != file_count or payload.get("timezone") != "Asia/Shanghai":
        print("[FULL_FUNCTION_GATE_INVALID] 门禁范围或时区记录不一致。", file=sys.stderr)
        return 2
    print(f"[FULL_FUNCTION_GATE_OK] {payload.get('tested_at')} {fingerprint}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("record", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    return record(root) if args.mode == "record" else verify(root)


if __name__ == "__main__":
    raise SystemExit(main())
