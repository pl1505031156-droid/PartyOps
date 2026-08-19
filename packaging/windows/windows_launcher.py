"""Windows 桌面入口：依据 mode.json 启动个人、主机或协同模式。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from app import __version__
from app.setup_wizard import (
    HostStartupError,
    _start_windows_host_service,
    clear_windows_client_autostart,
    launch_personal,
    load_host_environment,
    wait_for_host_health,
)

WIZARD_WAIT_SECONDS = 180.0
WIZARD_POLL_SECONDS = 0.5


def detached(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )


def read_loopback_tool_url(marker: Path) -> str:
    """只接受向导写入的单行 127.0.0.1 临时地址。"""

    try:
        if marker.is_symlink() or marker.stat().st_size > 2048:
            return ""
        lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if len(lines) != 1:
        return ""
    try:
        parsed = urllib.parse.urlparse(lines[0].strip())
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or not 1 <= port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return lines[0].strip()


def _rotate_bounded_log(
    path: Path, *, max_bytes: int = 5 * 1024 * 1024, backups: int = 5
) -> None:
    try:
        if not path.is_file() or path.stat().st_size < max_bytes:
            return
        path.with_name(f"{path.name}.{backups}").unlink(missing_ok=True)
        for index in range(backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        return


def _local_tool_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # nosec B310 - URL 已限制为 127.0.0.1。
            return 200 <= int(response.status) < 500
    except (OSError, ValueError):
        return False


def _try_launcher_lock(path: Path):
    """获取当前用户配置目录内的一字节互斥锁；返回 (句柄, 是否持有)。"""

    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    if os.name != "nt":
        return handle, True
    import msvcrt

    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle, True
    except OSError:
        return handle, False


def _release_launcher_lock(handle, owned: bool) -> None:
    if owned and os.name == "nt":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    handle.close()


def launch_wizard_and_wait(runtime: Path, local: Path, arguments: list[str]) -> bool:
    """启动无控制台向导并等待其真实页面地址，失败时保留中文日志。"""

    marker = local / "wizard.url"
    log_path = local / "launcher.log"
    local.mkdir(parents=True, exist_ok=True)
    _rotate_bounded_log(log_path)
    lock, owns_lock = _try_launcher_lock(local / "wizard.launch.lock")
    if not owns_lock:
        try:
            deadline = time.monotonic() + WIZARD_WAIT_SECONDS
            while time.monotonic() < deadline:
                url = read_loopback_tool_url(marker)
                if url and _local_tool_reachable(url):
                    return open_browser_or_explain(url)
                time.sleep(WIZARD_POLL_SECONDS)
            show_launch_failure(
                f"配置向导正在由另一个窗口启动，但 {int(WIZARD_WAIT_SECONDS)} 秒内未能显示页面。\n\n"
                f"请把日志复制给技术支持：{log_path}"
            )
            return False
        finally:
            _release_launcher_lock(lock, False)
    existing = read_loopback_tool_url(marker)
    if existing and _local_tool_reachable(existing):
        _release_launcher_lock(lock, True)
        return open_browser_or_explain(existing)
    marker.unlink(missing_ok=True)
    command = [str(runtime / "PartyOpsWizard.exe"), "--no-browser", *arguments]
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(  # noqa: S603 - 固定随包向导入口。
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW
                | subprocess.DETACHED_PROCESS,
            )
    except OSError as exc:
        try:
            with log_path.open("ab") as log:
                log.write(
                    f"\n[WIZARD_START_FAILED] {type(exc).__name__}: {exc}\n".encode(
                        "utf-8", errors="replace"
                    )
                )
        except OSError:
            pass
        show_launch_failure(
            "配置向导未能启动。请确认安装目录未被安全软件隔离。\n\n"
            f"诊断码：WIZARD_START_FAILED\n日志：{log_path}"
        )
        _release_launcher_lock(lock, True)
        return False
    try:
        deadline = time.monotonic() + WIZARD_WAIT_SECONDS
        while time.monotonic() < deadline:
            url = read_loopback_tool_url(marker)
            if url and _local_tool_reachable(url):
                return open_browser_or_explain(url)
            return_code = process.poll()
            if return_code is not None:
                show_launch_failure(
                    f"配置向导启动后提前退出（诊断码 WIZARD_EXIT_{return_code}）。\n\n"
                    f"请把日志复制给技术支持：{log_path}"
                )
                return False
            time.sleep(WIZARD_POLL_SECONDS)
        show_launch_failure(
            f"配置向导在 {int(WIZARD_WAIT_SECONDS)} 秒内未能显示页面。"
            "系统没有打开空白地址。\n\n"
            f"请把日志复制给技术支持：{log_path}"
        )
        return False
    finally:
        _release_launcher_lock(lock, True)


def versioned_browser_url(url: str) -> str:
    """为业务首页加入运行版本指纹，绕开升级前仍存活的旧浏览器页面。"""

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(key, value) for key, value in query if key != "partyops_runtime"]
    query.append(("partyops_runtime", __version__))
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def show_launch_failure(message: str) -> None:
    """在无控制台的桌面启动器中显示中文失败原因。"""

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            message,
            "党建智办启动失败",
            0x10,
        )
    except (AttributeError, OSError):
        # 极端精简系统可能无法显示 Win32 对话框；此处不能再次导致启动器崩溃。
        return


def open_browser_or_explain(url: str) -> bool:
    """打开已通过健康检查的页面；关联损坏时给出可复制地址。"""

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        show_launch_failure("系统生成的页面地址无效，请重新打开配置向导。")
        return False
    try:
        # Windows 的 ShellExecute 会使用系统默认浏览器，也兼容 Win7；优先于
        # Python 浏览器探测，避免注册表关联异常被静默吞掉。
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            startfile(url)
            return True
        if webbrowser.open(url):
            return True
    except (OSError, webbrowser.Error):
        pass
    show_launch_failure(
        "党建智办已经就绪，但系统默认浏览器未能打开。\n\n"
        f"请复制下面的地址到浏览器：\n{url}"
    )
    return False


def prepare_client_page(runtime: Path, config: Path, local: Path) -> bool:
    """同步准备协同页面，杜绝后台进程失败后桌面入口毫无反馈。"""

    marker = local / "client-browser.url"
    log_path = local / "client-agent.log"
    _rotate_bounded_log(log_path)
    marker.unlink(missing_ok=True)
    command = [
        str(runtime / "PartyOpsAgent.exe"),
        "--config",
        str(config),
        "--once",
        "--no-open-browser",
        "--browser-url-file",
        str(marker),
    ]
    try:
        with log_path.open("ab") as log:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=120,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        show_launch_failure(
            "协同终端在 120 秒内未能准备页面。\n\n"
            f"诊断日志：{log_path}"
        )
        return False
    if result.returncode != 0 or not marker.is_file():
        show_launch_failure(
            f"协同终端未能准备页面（诊断码 CLIENT_EXIT_{result.returncode}）。\n\n"
            f"诊断日志：{log_path}"
        )
        return False
    try:
        url = marker.read_text(encoding="utf-8").strip()
    except OSError:
        show_launch_failure("协同页面地址无法读取，请重新打开配置向导。")
        return False
    finally:
        marker.unlink(missing_ok=True)
    return open_browser_or_explain(versioned_browser_url(url))


def main() -> int:
    background = "--background" in sys.argv[1:]
    runtime = Path(sys.executable).resolve().parent
    local = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    )
    pending_switch = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps"
        / "host-switch-pending.json"
    )
    if pending_switch.is_file():
        # 上次停主机后若断电，任何角色都不得绕过恢复事务直接启动。
        if not background:
            launch_wizard_and_wait(runtime, local, [])
        return 1
    mode_path = local / "mode.json"
    if not mode_path.is_file():
        if not background:
            return 0 if launch_wizard_and_wait(runtime, local, []) else 1
        return 0
    try:
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        if not background:
            return 0 if launch_wizard_and_wait(runtime, local, []) else 1
        return 0
    if mode.get("mode") == "client":
        config = local / "client.json"
        if not config.is_file():
            if not background:
                return 0 if launch_wizard_and_wait(runtime, local, []) else 1
            return 0
        if background:
            detached(
                [
                    str(runtime / "PartyOpsAgent.exe"),
                    "--config",
                    str(config),
                    "--once",
                    "--no-open-browser",
                ]
            )
            return 0
        return 0 if prepare_client_page(runtime, config, local) else 1
    if mode.get("mode") == "personal":
        config = Path(str(mode.get("config_path") or local / "personal.env"))
        if not config.is_file():
            if not background:
                return (
                    0
                    if launch_wizard_and_wait(
                        runtime, local, ["--initial-role", "personal"]
                    )
                    else 1
                )
            return 1
        personal_log = local / "launcher.log"
        try:
            personal_values = load_host_environment(config)
            personal_log = (
                Path(personal_values["PARTYOPS_DATA_DIR"]) / "launcher.log"
            )
            url = launch_personal(config)
        except (HostStartupError, OSError, KeyError, ValueError) as exc:
            if not background:
                code = (
                    exc.code
                    if isinstance(exc, HostStartupError)
                    else "PERSONAL_CONFIG_INVALID"
                )
                message = (
                    str(exc)
                    if isinstance(exc, HostStartupError)
                    else "个人模式配置无法读取，系统没有继续启动。"
                )
                show_launch_failure(
                    f"个人模式未能启动（诊断码 {code}）。\n\n"
                    f"{message}\n诊断日志：{personal_log}"
                )
                launch_wizard_and_wait(
                    runtime, local, ["--initial-role", "personal"]
                )
            return 1
        if not background:
            return 0 if open_browser_or_explain(versioned_browser_url(url)) else 1
        return 0
    if mode.get("mode") == "host":
        clear_windows_client_autostart()
        config = Path(
            str(
                mode.get("config_path")
                or Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
                / "PartyOps"
                / "partyops.env"
            )
        )
        if not config.is_file():
            if not background:
                return 0 if launch_wizard_and_wait(runtime, local, []) else 1
            return 0
        try:
            values = load_host_environment(config)
            host = values.get("PARTYOPS_HOST", "127.0.0.1")
            port = int(values.get("PARTYOPS_PORT", "18765"))
            tls = values.get("PARTYOPS_TLS_ENABLED", "").lower() == "true"
            data_dir = Path(values["PARTYOPS_DATA_DIR"])
        except (OSError, KeyError, ValueError):
            if not background:
                show_launch_failure(
                    "主机配置无法读取（诊断码 HOST_CONFIG_INVALID）。\n\n"
                    "系统没有打开未就绪页面，请在配置向导中修复主机设置。"
                )
                launch_wizard_and_wait(runtime, local, ["--initial-role", "host"])
            return 1
        # 服务冷启动需要数十秒；先等服务健康再打开浏览器，
        # 避免首次配置提交时出现“目标计算机积极拒绝”（WinError 10061）。
        try:
            _start_windows_host_service()
            url = wait_for_host_health(
                host,
                port,
                tls=tls,
                timeout=180.0,
                data_dir=data_dir,
            )
        except (HostStartupError, OSError, subprocess.TimeoutExpired) as exc:
            # 严禁打开一个尚未就绪的地址；回到向导展示重试与诊断入口。
            code = (
                exc.code
                if isinstance(exc, HostStartupError)
                else "HOST_SERVICE_CONTROL_FAILED"
            )
            detail = (
                str(exc)
                if isinstance(exc, HostStartupError)
                else f"Windows 无法完成服务控制操作：{exc}"
            )
            if not background:
                show_launch_failure(
                    f"主机模式未能启动（诊断码 {code}）。\n\n"
                    f"{detail}\n诊断日志：{data_dir / 'logs'}"
                )
                launch_wizard_and_wait(runtime, local, ["--initial-role", "host"])
            return 1
        if not background:
            return 0 if open_browser_or_explain(versioned_browser_url(url)) else 1
        return 0
    if not background:
        return 0 if launch_wizard_and_wait(runtime, local, []) else 1
    return 0


def run_entrypoint_safely() -> int:
    """保证无控制台入口遇到未预料异常时仍给出可见中文诊断。"""

    emergency_root = Path(
        os.getenv("TEMP")
        or os.getenv("TMP")
        or os.getenv("LOCALAPPDATA")
        or Path.home()
    )
    emergency_log = emergency_root / "PartyOps-launcher-emergency.log"
    try:
        return main()
    except Exception:  # noqa: BLE001 - GUI 顶层兜底，异常详情写入本机诊断。
        log_written = False
        try:
            emergency_root.mkdir(parents=True, exist_ok=True)
            _rotate_bounded_log(emergency_log)
            with emergency_log.open("a", encoding="utf-8") as log:
                log.write(
                    f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    "Windows 桌面入口未处理异常 =====\n"
                )
                log.write(traceback.format_exc(limit=40))
            log_written = True
        except OSError:
            pass
        location = str(emergency_log) if log_written else "诊断日志也无法写入"
        show_launch_failure(
            "党建智办桌面入口遇到未预料错误，系统没有继续打开未就绪页面。\n\n"
            f"诊断码：LAUNCHER_UNHANDLED_ERROR\n诊断日志：{location}"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(run_entrypoint_safely())
