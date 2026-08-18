"""Windows 桌面入口：依据 mode.json 启动个人、主机或协同模式。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

from app.setup_wizard import (
    HostStartupError,
    _start_windows_host_service,
    clear_windows_client_autostart,
    launch_personal,
    load_host_environment,
    wait_for_host_health,
)


def detached(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
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
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        show_launch_failure(
            "协同终端在 120 秒内未能准备页面，请重新打开配置向导并查看诊断日志。"
        )
        return False
    if result.returncode != 0 or not marker.is_file():
        show_launch_failure(
            f"协同终端未能准备页面（诊断码 CLIENT_EXIT_{result.returncode}）。\n\n"
            "请重新打开配置向导并查看诊断日志。"
        )
        return False
    try:
        url = marker.read_text(encoding="utf-8").strip()
    except OSError:
        show_launch_failure("协同页面地址无法读取，请重新打开配置向导。")
        return False
    finally:
        marker.unlink(missing_ok=True)
    return open_browser_or_explain(url)


def main() -> int:
    background = "--background" in sys.argv[1:]
    runtime = Path(sys.executable).resolve().parent
    pending_switch = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps"
        / "host-switch-pending.json"
    )
    if pending_switch.is_file():
        # 上次停主机后若断电，任何角色都不得绕过恢复事务直接启动。
        if not background:
            detached([str(runtime / "PartyOpsWizard.exe")])
        return 1
    local = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    )
    mode_path = local / "mode.json"
    if not mode_path.is_file():
        if not background:
            detached([str(runtime / "PartyOpsWizard.exe")])
        return 0
    try:
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        if not background:
            detached([str(runtime / "PartyOpsWizard.exe")])
        return 0
    if mode.get("mode") == "client":
        config = local / "client.json"
        if not config.is_file():
            if not background:
                detached([str(runtime / "PartyOpsWizard.exe")])
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
                detached(
                    [str(runtime / "PartyOpsWizard.exe"), "--initial-role", "personal"]
                )
            return 1
        try:
            url = launch_personal(config)
        except HostStartupError:
            if not background:
                detached(
                    [str(runtime / "PartyOpsWizard.exe"), "--initial-role", "personal"]
                )
            return 1
        if not background:
            return 0 if open_browser_or_explain(url) else 1
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
                detached([str(runtime / "PartyOpsWizard.exe")])
            return 0
        values = load_host_environment(config)
        host = values.get("PARTYOPS_HOST", "127.0.0.1")
        port = int(values.get("PARTYOPS_PORT", "18765"))
        tls = values.get("PARTYOPS_TLS_ENABLED", "").lower() == "true"
        # 服务冷启动需要数十秒；先等服务健康再打开浏览器，
        # 避免首次配置提交时出现“目标计算机积极拒绝”（WinError 10061）。
        try:
            _start_windows_host_service()
            url = wait_for_host_health(
                host,
                port,
                tls=tls,
                timeout=180.0,
                data_dir=Path(values["PARTYOPS_DATA_DIR"]),
            )
        except HostStartupError:
            # 严禁打开一个尚未就绪的地址；回到向导展示重试与诊断入口。
            if not background:
                detached(
                    [str(runtime / "PartyOpsWizard.exe"), "--initial-role", "host"]
                )
            return 1
        if not background:
            return 0 if open_browser_or_explain(url) else 1
        return 0
    if not background:
        detached([str(runtime / "PartyOpsWizard.exe")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
