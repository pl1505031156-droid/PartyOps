"""PartyOps macOS 图形启动器：只在健康确认后打开浏览器。"""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


VERSION = "1.4.3-rc.9"


def _config_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "PartyOps" / "Config"


def _runtime_root() -> Path:
    return Path(sys.executable).resolve().parent


def _log_root() -> Path:
    return Path.home() / "Library" / "Logs" / "PartyOps"


def _rotate_log(path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return
        path.with_name(f"{path.name}.5").unlink(missing_ok=True)
        for index in range(4, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        return


def _append_log(message: str) -> None:
    root = _log_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "launcher.log"
    _rotate_log(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    path.chmod(0o600)


def _spawn_with_diagnostics(command: list[str]) -> subprocess.Popen[bytes]:
    """启动冻结子进程并保留有界日志，避免双击无反应时无证据。"""

    root = _log_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "runtime-launch.log"
    _rotate_log(path)
    handle = path.open("ab")
    try:
        process = subprocess.Popen(  # noqa: S603 - 只启动当前 app 内固定入口。
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        handle.close()
    path.chmod(0o600)
    return process


def _show_error(message: str) -> None:
    _append_log(message)
    script = (
        "on run argv\n"
        "display alert \"党建智办启动失败\" message (item 1 of argv) "
        "as critical buttons {\"知道了\"} default button \"知道了\"\n"
        "end run"
    )
    subprocess.run(
        ["/usr/bin/osascript", "-e", script, message],
        check=False,
        capture_output=True,
        timeout=30,
    )


def _open_url(url: str) -> None:
    result = subprocess.run(
        ["/usr/bin/open", url],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "PartyOps 已经就绪，但默认浏览器未能打开。请复制地址：" + url
        )


def _read_mode() -> dict[str, object] | None:
    path = _config_root() / "mode.json"
    if not path.is_file() or path.is_symlink():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise RuntimeError("本机 PartyOps 运行模式配置已损坏，请重新打开配置向导")
    return payload


def _read_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if separator and key.startswith("PARTYOPS_"):
            values[key] = shlex.split(raw)[0] if raw else ""
    return values


def _wait_for_wizard(process: subprocess.Popen[bytes]) -> str:
    marker = _config_root() / "wizard.url"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "配置向导未能启动。请查看 ~/Library/Logs/PartyOps/launcher.log"
            )
        try:
            url = marker.read_text(encoding="utf-8").strip()
        except OSError:
            url = ""
        if url.startswith("http://127.0.0.1:"):
            return url
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("配置向导在 60 秒内未就绪，系统没有打开空白页面")


def _wait_for_client_browser(process: subprocess.Popen[bytes], marker: Path) -> str:
    """只在协同 Agent 生成受控登录地址后打开浏览器。"""

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "协同进程未能启动。请查看 ~/Library/Logs/PartyOps/runtime-launch.log"
            )
        try:
            url = marker.read_text(encoding="utf-8").strip()
        except OSError:
            url = ""
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.fragment == ""
        ):
            marker.unlink(missing_ok=True)
            return url
        time.sleep(0.25)
    raise RuntimeError("协同进程在 90 秒内未准备好登录页面，系统没有打开无效地址")


def _wait_for_runtime(values: dict[str, str], expected_mode: str) -> str:
    port = int(values.get("PARTYOPS_PORT", "0"))
    if not 1024 <= port <= 65534:
        raise RuntimeError("PartyOps 服务端口配置无效，请重新配置")
    tls = values.get("PARTYOPS_TLS_ENABLED", "").lower() == "true"
    scheme = "https" if tls else "http"
    base_url = f"{scheme}://127.0.0.1:{port}"
    context = ssl._create_unverified_context() if tls else None  # nosec B323 - 仅访问固定回环健康端点。
    deadline = time.monotonic() + 180
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(  # nosec B310 - URL 固定为回环 HTTP(S)。
                f"{base_url}/api/v1/health", timeout=3, context=context
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if (
                payload.get("status") == "ok"
                and payload.get("app_version") == VERSION
                and payload.get("mode") == expected_mode
            ):
                return f"{base_url}/?partyops_runtime={int(time.time())}"
            if payload.get("status") == "ok" and payload.get("app_version") != VERSION:
                raise RuntimeError(
                    "检测到旧版 PartyOps 进程仍在运行，系统没有打开混合版本页面。"
                    "请退出旧进程后重试。"
                )
            last_error = "健康端点返回内容与当前模式不一致"
        except RuntimeError:
            raise
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(1.5)
    raise RuntimeError(
        "PartyOps 在 180 秒内未能就绪，系统没有打开未就绪页面。"
        f"最近诊断：{last_error or '服务未监听'}"
    )


def _launch(*, background: bool = False) -> None:
    runtime = _runtime_root()
    mode_payload = _read_mode()
    if mode_payload is None:
        # 启动事务已由 launcher.lock 串行化；先删除上次崩溃留下的
        # marker，禁止未就绪的新向导复用过期回环 URL。
        (_config_root() / "wizard.url").unlink(missing_ok=True)
        process = _spawn_with_diagnostics([str(runtime / "partyops-wizard")])
        url = _wait_for_wizard(process)
        if not background:
            _open_url(url)
        return

    mode = str(mode_payload.get("mode") or "")
    if mode not in {"host", "personal", "client"}:
        raise RuntimeError("PartyOps 运行角色无效，请重新打开配置向导")
    config_path = Path(str(mode_payload.get("config_path") or "")).expanduser().resolve()
    agent = runtime / "partyops-launch-agent"
    command = [str(agent), "--mode", mode]
    if mode == "client":
        if background:
            _spawn_with_diagnostics(command)
            return
        marker = _config_root() / "client-browser.url"
        marker.unlink(missing_ok=True)
        command.extend(["--browser-url-file", str(marker)])
        process = _spawn_with_diagnostics(command)
        _open_url(_wait_for_client_browser(process, marker))
        return
    values = _read_environment(config_path)
    _spawn_with_diagnostics(command)
    url = _wait_for_runtime(values, mode)
    if not background:
        _open_url(url)


def main() -> int:
    if sys.argv[1:] == ["--launch-services-self-test"]:
        _append_log(f"LaunchServices 已进入 PartyOps {VERSION} Python 桌面启动器")
        return 0
    if sys.argv[1:] == ["--self-test"]:
        required = (
            _runtime_root() / "partyops",
            _runtime_root() / "partyops-client",
            _runtime_root() / "partyops-wizard",
            _runtime_root() / "partyops-launch-agent",
            _runtime_root() / "partyops-updater",
        )
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            print("macOS 冻结运行时缺失：" + "、".join(missing), file=sys.stderr)
            return 2
        print(f"PartyOps macOS Launcher {VERSION} 自检通过。")
        return 0
    root = _config_root()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "launcher.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            _append_log(f"启动 PartyOps {VERSION}")
            _launch(background="--background" in sys.argv[1:])
        except Exception as exc:  # noqa: BLE001 - GUI 顶层必须转为中文可见诊断。
            _append_log(f"{type(exc).__name__}: {exc}")
            public = (
                str(exc)
                if isinstance(exc, RuntimeError) and str(exc).strip()
                else "PartyOps 启动组件未能完成。请查看日志并把最后 80 行发给技术支持。"
            )
            _show_error(public)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
