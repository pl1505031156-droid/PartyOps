"""Windows 桌面入口：仅依据 mode.json 启动主机或协同 Agent。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

from app.setup_wizard import load_host_environment


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
        config = Path(str(mode.get("config_path") or Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "partyops.env"))
        if not config.is_file():
            detached([str(runtime / "PartyOpsWizard.exe")])
            return 0
        subprocess.run(["sc.exe", "start", "PartyOpsHost"], check=False, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        values = load_host_environment(config)
        scheme = "https" if values.get("PARTYOPS_TLS_ENABLED", "").lower() == "true" else "http"
        webbrowser.open(f"{scheme}://{values.get('PARTYOPS_HOST', '127.0.0.1')}:{values.get('PARTYOPS_PORT', '18765')}")
        return 0
    detached([str(runtime / "PartyOpsWizard.exe")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
