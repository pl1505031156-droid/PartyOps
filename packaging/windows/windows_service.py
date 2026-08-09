"""Windows 主机服务入口：等待显式 host 配置后托管 PartyOps 主机进程。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

from app.setup_wizard import load_host_environment


class PartyOpsHostService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PartyOpsHost"
    _svc_display_name_ = "党建智办 PartyOps 主机服务"
    _svc_description_ = "在 Windows 10/11 上托管 PartyOps 局域网协同主机。"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process: subprocess.Popen | None = None

    def SvcStop(self):  # noqa: N802 - Windows 服务协议固定命名。
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self._stop_child_process()

    def _stop_child_process(self) -> None:
        """终止服务创建的完整进程树，避免冻结运行时在卸载后残留。"""
        if not self.process or self.process.poll() is not None:
            return
        subprocess.run(
            ["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def SvcDoRun(self):  # noqa: N802
        servicemanager.LogInfoMsg("PartyOpsHost 服务已启动，等待显式 host 配置。")
        program_data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
        config_path = program_data / "partyops.env"
        executable = Path(sys.executable).resolve().with_name("PartyOps.exe")
        while win32event.WaitForSingleObject(self.stop_event, 5000) == win32event.WAIT_TIMEOUT:
            if not config_path.is_file() or not executable.is_file():
                continue
            if self.process and self.process.poll() is None:
                continue
            environment = os.environ.copy()
            environment.update(load_host_environment(config_path))
            environment.setdefault("PARTYOPS_MODE", "host")
            environment.setdefault("PARTYOPS_DATA_DIR", str(program_data))
            self.process = subprocess.Popen(
                [str(executable)],
                env=environment,
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        if self.process and self.process.poll() is None:
            self._stop_child_process()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # 冻结后的服务进程由 SCM 无参数启动，必须显式进入服务分派器；
        # HandleCommandLine 只负责 install/start/debug 等维护命令。
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PartyOpsHostService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        raise SystemExit(win32serviceutil.HandleCommandLine(PartyOpsHostService))
