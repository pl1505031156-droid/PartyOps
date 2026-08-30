#!/usr/bin/env python3
"""记录并校验“全功能测试通过后才允许打包/发布”的双范围门禁。

官网源码按既有发布边界不进入 PartyOps 安装包仓库，因此原生构建机的干净
检出不会携带 ``website``。门禁分别冻结安装包与官网源码指纹：平台构建只
校验安装包范围，官网发布只校验官网范围，本机最终审查仍校验二者全集。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

GATE_RELATIVE_PATH = Path(".release-gates/full-function-tests.json")
BEIJING = timezone(timedelta(hours=8))
PACKAGE_INCLUDE_ROOTS = (
    "backend/app",
    "backend/tests",
    "frontend/src",
    "frontend/tests",
    "packaging",
    "scripts",
)
PACKAGE_INCLUDE_FILES = (
    "backend/pyproject.toml",
    "backend/requirements.txt",
    "backend/requirements-release.txt",
    "backend/uv.lock",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/vite.config.ts",
)
WEBSITE_INCLUDE_ROOTS = (
    "website/src",
    "website/edge-functions",
    "website/tests",
)
WEBSITE_INCLUDE_FILES = (
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
TEXT_SUFFIXES = {
    ".c",
    ".cmake",
    ".css",
    ".desktop",
    ".example",
    ".iss",
    ".isl",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".mjs",
    ".plist",
    ".policy",
    ".ps1",
    ".py",
    ".service",
    ".sh",
    ".spec",
    ".svg",
    ".toml",
    ".ts",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {"postinstall", "preinstall"}
FINGERPRINT_CANONICALIZATION = "text-lf-v1"


def _candidate_files(
    root: Path,
    include_roots: tuple[str, ...],
    include_files: tuple[str, ...],
) -> list[Path]:
    files: set[Path] = set()
    for relative in include_roots:
        base = root / relative
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts):
                files.add(path)
    for relative in include_files:
        path = root / relative
        if path.is_file():
            files.add(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _canonical_payload(path: Path) -> bytes:
    """规范源码文本换行，二进制仍按原始字节冻结。

    Git 在 Windows 的 ``core.autocrlf`` 会把工作树文本改为 CRLF，而 macOS
    原生检出保持 LF。两者语义相同，不应让已通过的测试门禁在另一平台误报；
    图片、DOCX 等二进制则必须继续逐字节校验。
    """

    payload = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILENAMES:
        return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return payload


def _fingerprint(root: Path, files: list[Path]) -> tuple[str, int]:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = _canonical_payload(path)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest(), len(files)


def _scope_files(root: Path, scope: str) -> list[Path]:
    package_files = _candidate_files(root, PACKAGE_INCLUDE_ROOTS, PACKAGE_INCLUDE_FILES)
    website_files = _candidate_files(root, WEBSITE_INCLUDE_ROOTS, WEBSITE_INCLUDE_FILES)
    if scope == "package":
        return package_files
    if scope == "website":
        return website_files
    return sorted(
        {*package_files, *website_files},
        key=lambda item: item.relative_to(root).as_posix(),
    )


def source_fingerprint(root: Path, scope: str = "full") -> tuple[str, int]:
    """返回指定发布范围的稳定内容指纹。"""

    return _fingerprint(root, _scope_files(root, scope))


def _fingerprint_record(root: Path, scope: str) -> dict[str, object]:
    fingerprint, file_count = source_fingerprint(root, scope)
    return {"sha256": fingerprint, "file_count": file_count}


def record(root: Path) -> int:
    source_fingerprints = {
        scope: _fingerprint_record(root, scope)
        for scope in ("package", "website", "full")
    }
    if any(source_fingerprints[scope]["file_count"] == 0 for scope in ("package", "website")):
        print(
            "[FULL_FUNCTION_GATE_SCOPE_MISSING] 安装包或官网源码范围为空，拒绝记录不完整测试门禁。",
            file=sys.stderr,
        )
        return 2
    target = root / GATE_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 3,
        "status": "passed",
        "tested_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "fingerprint_canonicalization": FINGERPRINT_CANONICALIZATION,
        "source_fingerprints": source_fingerprints,
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
    print(
        "[FULL_FUNCTION_GATE_RECORDED] "
        f"{target} package={source_fingerprints['package']['sha256']} "
        f"website={source_fingerprints['website']['sha256']}"
    )
    return 0


def verify(root: Path, scope: str) -> int:
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
    if payload.get("schema") != 3 or payload.get("status") != "passed":
        print("[FULL_FUNCTION_GATE_INVALID] 门禁版本或状态无效。", file=sys.stderr)
        return 2
    if payload.get("fingerprint_canonicalization") != FINGERPRINT_CANONICALIZATION:
        print("[FULL_FUNCTION_GATE_INVALID] 门禁指纹规范化版本无效。", file=sys.stderr)
        return 2
    fingerprints = payload.get("source_fingerprints")
    expected = fingerprints.get(scope) if isinstance(fingerprints, dict) else None
    if not isinstance(expected, dict):
        print(f"[FULL_FUNCTION_GATE_INVALID] 门禁缺少 {scope} 范围。", file=sys.stderr)
        return 2
    fingerprint, file_count = source_fingerprint(root, scope)
    if expected.get("sha256") != fingerprint or expected.get("file_count") != file_count:
        print(
            f"[FULL_FUNCTION_GATE_STALE:{scope}] 测试后源码已变化；必须重新运行 "
            "scripts/test.ps1，拒绝生成平台安装包或发布官网。",
            file=sys.stderr,
        )
        return 2
    if payload.get("timezone") != "Asia/Shanghai":
        print("[FULL_FUNCTION_GATE_INVALID] 门禁范围或时区记录不一致。", file=sys.stderr)
        return 2
    print(f"[FULL_FUNCTION_GATE_OK:{scope}] {payload.get('tested_at')} {fingerprint}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("record", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", choices=("full", "package", "website"), default="full")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "record":
        if args.scope != "full":
            parser.error("record 只能记录 full 范围")
        return record(root)
    return verify(root, args.scope)


if __name__ == "__main__":
    raise SystemExit(main())
