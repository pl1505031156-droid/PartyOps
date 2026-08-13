"""Windows 系统级更新服务：以 LocalSystem 托管受限更新执行器。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

from app.setup_wizard import load_host_environment


class PartyOpsUpdaterService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PartyOpsUpdateService"
    _svc_display_name_ = "党建智办 PartyOps 更新服务"
    _svc_description_ = "校验签名后执行 PartyOps 更新、健康检查和失败回滚。"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process: subprocess.Popen | None = None

    def SvcStop(self):  # noqa: N802 - Windows 服务协议固定命名。
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self._stop_child_process()

    def _stop_child_process(self) -> None:
        """终止更新器完整进程树，确保升级和卸载不会留下锁文件。"""
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
        runtime = Path(sys.executable).resolve().parent
        updater = runtime / "PartyOpsUpdater.exe"
        program_data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
        config_path = program_data / "partyops.env"
        servicemanager.LogInfoMsg("PartyOpsUpdateService 已启动。")
        while win32event.WaitForSingleObject(self.stop_event, 3000) == win32event.WAIT_TIMEOUT:
            if not updater.is_file() or not config_path.is_file():
                continue
            if self.process and self.process.poll() is None:
                continue
            environment = os.environ.copy()
            environment.update(load_host_environment(config_path))
            environment.setdefault("PARTYOPS_MODE", "host")
            environment.setdefault("PARTYOPS_ENVIRONMENT", "production")
            environment.setdefault("PARTYOPS_STRICT_SQLITE", "true")
            data_dir = Path(environment["PARTYOPS_DATA_DIR"])
            log_path = data_dir / "logs" / "partyops-updater-service.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            output = log_path.open("ab", buffering=0)
            self.process = subprocess.Popen(
                [str(updater)],
                env=environment,
                cwd=str(runtime),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output.close()
        if self.process and self.process.poll() is None:
            self._stop_child_process()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # SCM 启动服务时不会传入命令；冻结程序必须主动注册服务分派器。
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PartyOpsUpdaterService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        raise SystemExit(win32serviceutil.HandleCommandLine(PartyOpsUpdaterService))
