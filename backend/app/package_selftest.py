"""原生安装包配置阶段使用的离线运行时自检。"""

from __future__ import annotations

import importlib
import json
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path


ASSET_PATTERN = re.compile(r"(?:src|href)=[\"']/?([^\"'#?]+)")


def _runtime_contents(runtime: Path) -> Path:
    if sys.platform == "darwin":
        # PyInstaller 的 macOS BUNDLE 会按 Apple 目录约定把数据放在
        # Contents/Resources，而 Mach-O 入口位于 Contents/MacOS。不能
        # 假设 Resources 一定经符号链接映射回可执行目录；普通 APFS、
        # ZIP 往返和 Installer 都可能暴露这种错误假设。
        resources = runtime.parent / "Resources"
        if resources.is_dir():
            return resources
    internal = runtime / "_internal"
    return internal if internal.is_dir() else runtime


def _native_executable(runtime: Path, relative: str) -> Path:
    """按当前系统返回冻结原生程序名，避免 Windows 自检误找无后缀文件。"""

    candidate = runtime / relative
    return candidate.with_suffix(".exe") if os.name == "nt" else candidate


def _ocr_runtime(runtime: Path) -> tuple[Path, Path, Path]:
    """返回 OCR 可执行文件、词库与动态库目录，遵守各平台包布局。"""

    if sys.platform == "darwin":
        resources = runtime.parent / "Resources" / "ocr"
        return runtime / "tesseract", resources / "tessdata", resources / "lib"
    root = runtime / "ocr"
    return _native_executable(runtime, "ocr/bin/tesseract"), root / "tessdata", root / "lib"


def run_selftest(runtime: Path) -> dict[str, object]:
    """验证冻结资源、数据库、OCR 与本地智能运行时，任一失败即抛错。"""

    contents = _runtime_contents(runtime)
    frontend = contents / "frontend"
    index = frontend / "index.html"
    if not index.is_file():
        raise RuntimeError("前端入口缺失")
    html = index.read_text(encoding="utf-8")
    missing_assets = sorted(
        asset for asset in ASSET_PATTERN.findall(html) if not (frontend / asset).is_file()
    )
    if missing_assets:
        raise RuntimeError(f"前端静态资源缺失：{', '.join(missing_assets)}")

    from app.database import db_runtime

    sqlite = db_runtime.validate_capabilities()
    if not sqlite.get("safe_version") or not sqlite.get("fts5"):
        raise RuntimeError("SQLite 安全版本或 FTS5 自检失败")

    ocr, tessdata, ocr_library = _ocr_runtime(runtime)
    language = tessdata / "chi_sim.traineddata"
    if not ocr.is_file() or not language.is_file():
        raise RuntimeError("中文 OCR 运行时不完整")
    ocr_environment = os.environ.copy()
    ocr_environment["TESSDATA_PREFIX"] = str(language.parent)
    ocr_environment["LD_LIBRARY_PATH"] = os.pathsep.join(
        part
        for part in (
            str(ocr_library),
            ocr_environment.get("LD_LIBRARY_PATH", ""),
        )
        if part
    )
    ocr_result = subprocess.run(
        [str(ocr), "--list-langs"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ocr_environment,
        timeout=30,
    )
    if ocr_result.returncode != 0 or "chi_sim" not in ocr_result.stdout.split():
        raise RuntimeError("中文 OCR 语言包无法加载")

    smart_versions: dict[str, str] = {}
    for package in ("numpy", "onnxruntime", "tokenizers"):
        module = importlib.import_module(package)
        smart_versions[package] = str(getattr(module, "__version__", "unknown"))
    # macOS Intel 的 setup-python 可能携带只服务旧算法的 OpenSSL legacy
    # provider；发布包会剔除它。这里显式验证 PartyOps 实际使用的现代 TLS 与
    # Ed25519 签名链，防止闭包瘦身造成“能安装但加密功能启动时才失败”。
    ssl.create_default_context()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    crypto_key = Ed25519PrivateKey.generate()
    crypto_payload = b"PartyOps package self-test"
    crypto_key.public_key().verify(crypto_key.sign(crypto_payload), crypto_payload)
    llama = _native_executable(runtime, "llama-server")
    if not llama.is_file():
        raise RuntimeError("本地 LLM 运行时缺失")
    llama_environment = os.environ.copy()
    llama_environment["LD_LIBRARY_PATH"] = os.pathsep.join(
        part
        for part in (str(runtime), llama_environment.get("LD_LIBRARY_PATH", ""))
        if part
    )
    llama_result = subprocess.run(
        [str(llama), "--version"],
        check=False,
        capture_output=True,
        env=llama_environment,
        timeout=30,
    )
    if llama_result.returncode != 0:
        raise RuntimeError("本地 LLM 运行时无法启动")

    return {
        "passed": True,
        "architecture": os.uname().machine if hasattr(os, "uname") else "windows",
        "sqlite": sqlite,
        "frontend_assets": len(ASSET_PATTERN.findall(html)),
        "ocr": "chi_sim",
        "smart_runtime": smart_versions,
        "crypto": "tls-ed25519-passed",
        "llama": "passed",
    }


def main(runtime: Path | None = None) -> int:
    try:
        result = run_selftest(runtime or Path(sys.executable).resolve().parent)
    except Exception as exc:  # 自检入口必须返回稳定中文摘要，完整异常由调用方记录。
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
