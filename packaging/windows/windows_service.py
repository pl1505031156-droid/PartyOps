"""Windows 主机服务入口：等待显式 host 配置后托管 PartyOps 主机进程。"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

from app.setup_wizard import (
    assert_windows_service_data_path_security,
    load_host_environment,
    normalize_windows_service_data_path_security,
)
from app import __version__
from app.windows_host_status import (
    CHILD_EXITED,
    DATA_DIR_DENIED,
    HEALTH_TIMEOUT,
    PORT_IN_USE,
    TLS_INIT_FAILED,
    TERMINAL_CODES,
    probe_loopback_health,
    read_service_status,
    service_log_path,
    tail_service_log,
    write_service_status,
    classify_runtime_failure,
)


def _rotate_service_log(path: Path, *, max_bytes: int = 5 * 1024 * 1024) -> None:
    """在启动子进程前轮转日志，最多保留五份，避免服务日志占满数据盘。"""

    if not path.exists() or path.stat().st_size < max_bytes:
        return
    oldest = path.with_name(f"{path.name}.5")
    if oldest.exists():
        oldest.unlink()
    for index in range(4, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            os.replace(source, target)
    os.replace(path, path.with_name(f"{path.name}.1"))


def _append_service_log(data_dir: Path, message: str) -> None:
    path = service_log_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"{timestamp} service {message}\n")


def _safe_append_service_log(data_dir: Path, message: str) -> None:
    """日志目录异常时保留 Windows 事件，监督服务自身继续存活。"""

    try:
        _append_service_log(data_dir, message)
    except OSError as exc:
        servicemanager.LogErrorMsg(f"PartyOps 主机服务日志不可写：{exc}")


def _classify_child_failure(log_tail: str) -> str:
    return classify_runtime_failure(log_tail)


def _safe_write_service_status(data_dir: Path, **values) -> None:
    """状态文件不可写时保留 Windows 事件日志，不能让监督服务自身崩溃。"""

    try:
        write_service_status(data_dir, **values)
    except OSError as exc:
        servicemanager.LogErrorMsg(f"PartyOps 主机状态文件不可写：{exc}")


def prepare_host_runtime(environment: dict[str, str], executable: Path) -> None:
    """仅在明确选择主机后开放私网端口，并启动主机更新监督器。"""

    port = int(environment.get("PARTYOPS_PORT", "18765"))
    if not 1024 <= port <= 65534:
        raise RuntimeError("主机服务端口超出允许范围")
    rule_name = "党建智办主机"
    subprocess.run(
        [
            "netsh.exe",
            "advfirewall",
            "firewall",
            "delete",
            "rule",
            f"name={rule_name}",
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    firewall = subprocess.run(
        [
            "netsh.exe",
            "advfirewall",
            "firewall",
            "add",
            "rule",
            f"name={rule_name}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={port},{port + 1}",
            "profile=private",
            "remoteip=LocalSubnet",
            f"program={executable}",
            "enable=yes",
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if firewall.returncode != 0:
        raise RuntimeError("专用网络防火墙规则配置失败")
    # 协同机由 Agent 按用户确认更新；只有主机需要常驻系统级更新监督器。
    updater = subprocess.run(
        ["sc.exe", "start", "PartyOpsUpdateService"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    # 1056 表示服务已经运行；除此之外不能把主机伪装成“配置完成”，否则
    # 用户之后永远收不到更新且界面没有任何诊断。
    if updater.returncode not in {0, 1056}:
        raise RuntimeError("PartyOps 更新服务未能启动")


class PartyOpsHostService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PartyOpsHost"
    _svc_display_name_ = "党建智办 PartyOps 主机服务"
    _svc_description_ = "在 Windows 10/11 上托管 PartyOps 局域网协同主机。"

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process: subprocess.Popen | None = None
        self.output_stream = None
        self.child_environment: dict[str, str] | None = None
        self.child_data_dir: Path | None = None
        self.child_started_at = 0.0
        self.ready_pid: int | None = None
        self.health_timeout_pid: int | None = None

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

    def _close_output_stream(self) -> None:
        if self.output_stream:
            self.output_stream.close()
            self.output_stream = None

    def SvcDoRun(self):  # noqa: N802
        servicemanager.LogInfoMsg("PartyOpsHost 服务已启动，等待显式 host 配置。")
        program_data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
        config_path = program_data / "partyops.env"
        executable = Path(sys.executable).resolve().with_name("PartyOps.exe")
        prepared_config_mtime = 0
        while win32event.WaitForSingleObject(self.stop_event, 5000) == win32event.WAIT_TIMEOUT:
            if not config_path.is_file() or not executable.is_file():
                continue
            if self.process:
                exit_code = self.process.poll()
                if exit_code is None:
                    if (
                        self.ready_pid != self.process.pid
                        and self.child_environment is not None
                        and self.child_data_dir is not None
                    ):
                        port = int(self.child_environment.get("PARTYOPS_PORT", "18765"))
                        tls = (
                            self.child_environment.get("PARTYOPS_TLS_ENABLED", "").lower()
                            == "true"
                        )
                        healthy, detail = probe_loopback_health(
                            port,
                            tls=tls,
                            expected_version=__version__,
                            ca_file=(
                                self.child_data_dir / "secrets" / "pki" / "ca.pem"
                                if tls and self.child_data_dir is not None
                                else None
                            ),
                        )
                        if healthy:
                            _safe_write_service_status(
                                self.child_data_dir,
                                stage="ready",
                                pid=self.process.pid,
                            )
                            _safe_append_service_log(
                                self.child_data_dir,
                                f"主进程健康检查通过 pid={self.process.pid}",
                            )
                            self.ready_pid = self.process.pid
                        elif (
                            time.monotonic() - self.child_started_at >= 180
                            and self.health_timeout_pid != self.process.pid
                        ):
                            _safe_write_service_status(
                                self.child_data_dir,
                                stage="health_timeout",
                                code=HEALTH_TIMEOUT,
                                detail=detail,
                                pid=self.process.pid,
                            )
                            _safe_append_service_log(
                                self.child_data_dir,
                                f"主进程健康检查超时 pid={self.process.pid} detail={detail}",
                            )
                            self.health_timeout_pid = self.process.pid
                    continue
                self._close_output_stream()
                environment = os.environ.copy()
                environment.update(load_host_environment(config_path))
                data_dir = Path(environment.get("PARTYOPS_DATA_DIR", str(program_data)))
                detail = tail_service_log(data_dir)
                # 主进程在数据库迁移失败等场景会先写入稳定诊断。不能在进程
                # 退出后把它覆盖成泛化 CHILD_EXITED，否则用户又只能看到堆栈。
                existing = read_service_status(data_dir)
                existing_code = str((existing or {}).get("code", ""))
                if existing_code in TERMINAL_CODES:
                    code = existing_code
                    detail = str((existing or {}).get("detail", "")) or detail
                else:
                    code = classify_runtime_failure(detail, exit_code=exit_code)
                _safe_write_service_status(
                    data_dir,
                    stage="child_exited",
                    code=code,
                    detail=detail,
                    exit_code=exit_code,
                )
                _safe_append_service_log(data_dir, f"主进程退出 exit_code={exit_code} code={code}")
                self.process = None
                self.child_environment = None
                self.child_data_dir = None
                self.ready_pid = None
                self.health_timeout_pid = None
                # 让向导至少有一个轮询周期读取稳定的终止诊断，再进行自动重启。
                continue
            environment = os.environ.copy()
            environment.update(load_host_environment(config_path))
            environment.setdefault("PARTYOPS_MODE", "host")
            environment.setdefault("PARTYOPS_DATA_DIR", str(program_data))
            # Windows 服务没有交互控制台，冻结 Python 仍可能继承系统代码页。
            # 强制 UTF-8 后，中文异常与监督服务写入的 UTF-8 日志保持一致。
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUNBUFFERED"] = "1"
            data_dir = Path(environment["PARTYOPS_DATA_DIR"])
            try:
                data_dir.mkdir(parents=True, exist_ok=True)
                try:
                    assert_windows_service_data_path_security(
                        data_dir,
                        verify_target=True,
                    )
                except PermissionError as legacy_acl_error:
                    try:
                        normalize_windows_service_data_path_security(data_dir)
                        _safe_append_service_log(
                            data_dir,
                            "已安全升级旧版自定义数据目录权限",
                        )
                    except (OSError, PermissionError, ValueError) as repair_error:
                        raise PermissionError(
                            f"{legacy_acl_error}；自动修复失败：{repair_error}"
                        ) from repair_error
                _safe_write_service_status(data_dir, stage="preparing")
                config_mtime = config_path.stat().st_mtime_ns
                if prepared_config_mtime != config_mtime:
                    prepare_host_runtime(environment, executable)
                    prepared_config_mtime = config_mtime
            except PermissionError as exc:
                _safe_write_service_status(
                    data_dir,
                    stage="prepare_failed",
                    code=DATA_DIR_DENIED,
                    detail=str(exc),
                )
                servicemanager.LogErrorMsg(f"PartyOps 数据目录不可用：{exc}")
                continue
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
                _safe_write_service_status(
                    data_dir,
                    stage="prepare_failed",
                    code=CHILD_EXITED,
                    detail=str(exc),
                )
                servicemanager.LogErrorMsg(f"PartyOps 主机系统配置未完成：{exc}")
                continue
            try:
                log_path = service_log_path(data_dir)
                _rotate_service_log(log_path)
                _safe_append_service_log(data_dir, "正在启动 PartyOps 主进程")
                self.output_stream = log_path.open("ab", buffering=0)
                self.process = subprocess.Popen(
                    [str(executable)],
                    env=environment,
                    cwd=str(executable.parent),
                    stdin=subprocess.DEVNULL,
                    stdout=self.output_stream,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.child_environment = environment
                self.child_data_dir = data_dir
                self.child_started_at = time.monotonic()
                self.ready_pid = None
                self.health_timeout_pid = None
            except OSError as exc:
                self._close_output_stream()
                code = classify_runtime_failure(
                    str(exc),
                    winerror=getattr(exc, "winerror", None),
                )
                _safe_write_service_status(
                    data_dir,
                    stage="child_exited",
                    code=code,
                    detail=str(exc),
                )
                servicemanager.LogErrorMsg(f"PartyOps 主进程无法启动：{exc}")
                self.process = None
                self.child_environment = None
                self.child_data_dir = None
                continue
            _safe_write_service_status(
                data_dir,
                stage="child_running",
                pid=self.process.pid,
            )
        if self.process and self.process.poll() is None:
            self._stop_child_process()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._close_output_stream()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # 冻结后的服务进程由 SCM 无参数启动，必须显式进入服务分派器；
        # HandleCommandLine 只负责 install/start/debug 等维护命令。
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PartyOpsHostService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        raise SystemExit(win32serviceutil.HandleCommandLine(PartyOpsHostService))
