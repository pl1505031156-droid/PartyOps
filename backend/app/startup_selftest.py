"""在目标 Windows 电脑上验证冻结主程序能够真实启动。"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _reserve_loopback_port() -> int:
    """取得一个临时回环端口；后续子进程仍会以独占绑定作最终裁决。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _critical_crypto_roundtrip() -> None:
    """覆盖 Win7 安全回移最容易在启动期失败的 RSA 与 Fernet 路径。"""

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    payload = b"PartyOps target runtime startup self-test"
    oaep = padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )
    if private_key.decrypt(public_key.encrypt(payload, oaep), oaep) != payload:
        raise RuntimeError("RSA OAEP 目标机往返校验失败")
    numbers = private_key.private_numbers()
    recovered = rsa.rsa_recover_private_exponent(
        numbers.public_numbers.e,
        numbers.p,
        numbers.q,
    )
    if recovered != numbers.d:
        raise RuntimeError("RSA 私钥恢复兼容路径校验失败")
    fernet = Fernet(Fernet.generate_key())
    if fernet.decrypt(fernet.encrypt(payload)) != payload:
        raise RuntimeError("Fernet 目标机往返校验失败")


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - limit))
            return stream.read(limit).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310 - URL 固定为回环地址。
        if response.status != 200:
            raise RuntimeError(f"目标机健康端点返回 HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _read_frontend(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310 - URL 固定为回环地址。
        if response.status != 200:
            raise RuntimeError(f"目标机首页返回 HTTP {response.status}")
        return response.read(1024 * 1024)


def _validate_probe(health: dict[str, object], frontend: bytes) -> None:
    from . import __version__

    if health.get("status") != "ok":
        raise RuntimeError("目标机健康端点未返回 ok")
    if health.get("app_version") != __version__:
        raise RuntimeError(
            f"目标机运行版本不一致：{health.get('app_version') or '<missing>'}"
        )
    if health.get("mode") != "personal":
        raise RuntimeError(
            f"目标机自检模式不一致：{health.get('mode') or '<missing>'}"
        )
    sqlite_info = health.get("sqlite")
    if not isinstance(sqlite_info, dict) or not (
        sqlite_info.get("safe_version") is True and sqlite_info.get("fts5") is True
    ):
        raise RuntimeError("目标机 SQLite 安全版本或 FTS5 未通过")
    lowered = frontend.lower()
    if b"<!doctype html" not in lowered or b'id="app"' not in lowered:
        raise RuntimeError("目标机首页静态入口不完整")


def _probe_frozen_server(runtime: Path, timeout: float) -> dict[str, object]:
    """以临时数据目录启动同一个冻结 EXE，并等待完整健康与首页响应。"""

    port = _reserve_loopback_port()
    with tempfile.TemporaryDirectory(prefix="partyops-startup-selftest-") as temporary:
        root = Path(temporary).resolve()
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
        environment = os.environ.copy()
        environment.update(
            {
                "PARTYOPS_DATA_DIR": str(root / "data"),
                "PARTYOPS_MODE": "personal",
                "PARTYOPS_ENVIRONMENT": "production",
                "PARTYOPS_HOST": "127.0.0.1",
                "PARTYOPS_BIND_HOST": "127.0.0.1",
                "PARTYOPS_ADVERTISE_HOST": "127.0.0.1",
                "PARTYOPS_PORT": str(port),
                # 目标机探针只验证个人模式；复用主端口可禁止额外 Agent 监听。
                "PARTYOPS_AGENT_PORT": str(port),
                "PARTYOPS_LOCAL_AI_PORT": str(port),
                "PARTYOPS_TLS_ENABLED": "false",
                "PYTHONUTF8": "1",
            }
        )
        frontend = runtime / "_internal" / "frontend"
        if frontend.is_dir():
            environment["PARTYOPS_FRONTEND_DIST"] = str(frontend)
        creationflags = (
            int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        )
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(  # noqa: S603 - 只启动当前已安装的冻结主程序。
                [sys.executable, "--startup-self-test-child"],
                cwd=runtime,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
            )
        deadline = time.monotonic() + max(10.0, timeout)
        last_error = "尚未监听"
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    detail = _tail(stderr_path) or _tail(stdout_path)
                    raise RuntimeError(
                        f"冻结主程序提前退出（退出码 {process.returncode}）："
                        f"{detail or '没有产生子进程日志'}"
                    )
                try:
                    health = _read_json(f"http://127.0.0.1:{port}/api/v1/health")
                    page = _read_frontend(f"http://127.0.0.1:{port}/")
                    _validate_probe(health, page)
                    return health
                except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError, RuntimeError) as exc:
                    last_error = str(exc)
                time.sleep(0.5)
            detail = _tail(stderr_path) or _tail(stdout_path)
            raise RuntimeError(
                f"冻结主程序在 {int(timeout)} 秒内未就绪：{last_error}；"
                f"子进程日志：{detail or '无'}"
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def run_selftest(runtime: Path, timeout: float = 120.0) -> dict[str, object]:
    """执行无持久副作用的目标机加密与完整启动探针。"""

    _critical_crypto_roundtrip()
    health = _probe_frozen_server(runtime.resolve(), timeout)
    return {
        "passed": True,
        "version": health.get("app_version"),
        "mode": health.get("mode"),
        "crypto": "rsa+fernet",
        "database": "sqlite+fts5",
        "frontend": "ready",
    }


def run_user_permission_selftest(runtime: Path) -> dict[str, object]:
    """以原桌面账号核验安装树可读执行及用户临时目录可原子写入。

    安装器主体由管理员令牌运行，不能代表真正双击桌面图标的标准账号。该轻量
    探针由 Inno Setup 通过 ExecAsOriginalUser 调用，因此入口能够执行本身已
    证明程序目录具有读取/执行权限，再补充关键资源和用户写入路径核验。
    """

    resolved = runtime.resolve()
    candidates = [Path(sys.executable).resolve()]
    frontend = resolved / "_internal" / "frontend" / "index.html"
    if frontend.is_file():
        candidates.append(frontend)
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_file():
            raise PermissionError(f"安装资源不是受控普通文件：{candidate}")
        with candidate.open("rb") as stream:
            if not stream.read(1):
                raise PermissionError(f"安装资源为空或不可读：{candidate}")

    with tempfile.TemporaryDirectory(prefix="partyops-user-permission-") as temporary:
        root = Path(temporary).resolve()
        pending = root / "permission.tmp"
        completed = root / "permission.ok"
        pending.write_bytes(b"PartyOps user permission self-test")
        pending.replace(completed)
        if completed.read_bytes() != b"PartyOps user permission self-test":
            raise PermissionError("桌面账号临时目录原子写入回读失败")
        completed.unlink()
    return {
        "passed": True,
        "mode": "desktop-user-permission",
        "runtime_readable": True,
        "user_temp_writable": True,
    }


def main(runtime: Path | None = None) -> int:
    try:
        result = run_selftest(runtime or Path(sys.executable).resolve().parent)
    except Exception as exc:  # 目标机安装日志需要稳定、可复制的单行 JSON。
        print(
            json.dumps(
                {
                    "passed": False,
                    "code": "PACKAGE_RUNTIME_STARTUP_SELFTEST_FAILED",
                    "error": str(exc)[-6000:],
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
