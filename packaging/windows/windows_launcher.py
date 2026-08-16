"""Windows 桌面入口：依据 mode.json 启动个人、主机或协同模式。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
        detached(
            [str(runtime / "PartyOpsAgent.exe"), "--config", str(config), "--once"]
        )
        return 0
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
            webbrowser.open(url)
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
            webbrowser.open(url)
        return 0
    if not background:
        detached([str(runtime / "PartyOpsWizard.exe")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
