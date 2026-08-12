"""Windows 桌面入口：仅依据 mode.json 启动主机或协同 Agent。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from app.setup_wizard import (
    clear_windows_client_autostart,
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
    runtime = Path(sys.executable).resolve().parent
    local = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    mode_path = local / "mode.json"
    if not mode_path.is_file():
        detached([str(runtime / "PartyOpsWizard.exe")])
        return 0
    try:
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        detached([str(runtime / "PartyOpsWizard.exe")])
        return 0
    if mode.get("mode") == "client":
        config = local / "client.json"
        if not config.is_file():
            detached([str(runtime / "PartyOpsWizard.exe")])
            return 0
        detached([str(runtime / "PartyOpsAgent.exe"), "--config", str(config), "--once"])
        return 0
    if mode.get("mode") == "host":
        clear_windows_client_autostart()
        config = Path(str(mode.get("config_path") or Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "partyops.env"))
        if not config.is_file():
            detached([str(runtime / "PartyOpsWizard.exe")])
            return 0
        subprocess.run(["sc.exe", "start", "PartyOpsHost"], check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        values = load_host_environment(config)
        host = values.get("PARTYOPS_HOST", "127.0.0.1")
        port = int(values.get("PARTYOPS_PORT", "18765"))
        tls = values.get("PARTYOPS_TLS_ENABLED", "").lower() == "true"
        # 服务冷启动需要数十秒；先等服务健康再打开浏览器，
        # 避免首次配置提交时出现“目标计算机积极拒绝”（WinError 10061）。
        try:
            url = wait_for_host_health(host, port, tls=tls)
        except ConnectionError:
            scheme = "https" if tls else "http"
            url = f"{scheme}://{host}:{port}"
        webbrowser.open(url)
        return 0
    detached([str(runtime / "PartyOpsWizard.exe")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
