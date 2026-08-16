"""首次配置向导的系统选择器、主机启动与命令行分派回归。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard


def test_choose_system_folder_windows_success_cancel_and_failure(monkeypatch, tmp_path) -> None:
    selected = tmp_path / "共享目录"
    selected.mkdir()

    class _Root:
        def withdraw(self):
            return None

        def attributes(self, *_args):
            return None

        def destroy(self):
            return None

    filedialog = SimpleNamespace(askdirectory=lambda **_kwargs: str(selected))
    tkinter = SimpleNamespace(Tk=lambda: _Root(), filedialog=filedialog)
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", filedialog)
    assert setup_wizard._choose_system_folder() == selected

    filedialog.askdirectory = lambda **_kwargs: ""
    assert setup_wizard._choose_system_folder() is None
    tkinter.Tk = lambda: (_ for _ in ()).throw(RuntimeError("desktop unavailable"))
    assert setup_wizard._choose_system_folder() is None


def test_choose_system_folder_linux_zenity_kdialog_and_no_selector(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(setup_wizard, "os", SimpleNamespace(name="posix"))
    selected = tmp_path / "资料"
    selected.mkdir()
    monkeypatch.setattr(
        setup_wizard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "zenity" else None,
    )
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, f"{selected}\n", ""),
    )
    assert setup_wizard._choose_system_folder() == selected

    monkeypatch.setattr(setup_wizard.shutil, "which", lambda _name: None)
    assert setup_wizard._choose_system_folder() is None


def test_windows_ca_install_and_wait_copy(monkeypatch, tmp_path) -> None:
    config = tmp_path / "config"
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(setup_wizard.sys, "platform", "win32")
    ca = tmp_path / "ca.pem"
    assert setup_wizard.install_internal_ca(ca) is None
    ca.write_text("TEST CA", encoding="utf-8")

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "", ""),
    )
    assert setup_wizard.install_internal_ca(ca) is None
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "denied"),
    )
    with pytest.raises(ValueError, match="证书安装未完成"):
        setup_wizard.install_internal_ca(ca)

    installed: list[Path] = []
    monkeypatch.setattr(setup_wizard, "install_internal_ca", installed.append)
    setup_wizard._wait_and_install_ca(ca)
    trusted = config / "pki" / "ca.pem"
    assert installed == [trusted]
    assert trusted.read_text(encoding="utf-8") == "TEST CA"


def test_launch_host_windows_service_success_test_fallback_and_production_failure(monkeypatch, tmp_path) -> None:
    data_dir = tmp_path / "data"
    config = tmp_path / "partyops.env"
    environment = {
        "PARTYOPS_HOST": "127.0.0.1",
        "PARTYOPS_PORT": "18765",
        "PARTYOPS_DATA_DIR": str(data_dir),
    }
    monkeypatch.setattr(setup_wizard, "load_host_environment", lambda _path: environment)
    monkeypatch.setattr(setup_wizard, "install_host_autostart", lambda _path: None)
    monkeypatch.setattr(setup_wizard, "_wait_and_install_ca", lambda _path: None)
    # launch_host 现在会等待主机健康检查后再打开浏览器；测试环境没有真实服务，直接返回预期地址。
    monkeypatch.setattr(
        setup_wizard,
        "wait_for_host_health",
        lambda host, port, **_kwargs: f"http://{host}:{port}",
    )
    monkeypatch.setattr(
        setup_wizard,
        "os",
        SimpleNamespace(name="nt", getenv=lambda key, default=None: "test" if key == "PARTYOPS_ENVIRONMENT" else default),
    )
    # test 环境：launch_host 不再调用 sc.exe，而是直接 spawn 用户进程。
    spawned: list[list[str]] = []
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: tmp_path / "partyops.exe")
    monkeypatch.setattr(setup_wizard, "_spawn", lambda command, *_args: spawned.append(command))
    assert setup_wizard.launch_host(config) == "http://127.0.0.1:18765"
    assert spawned

    # production 环境：服务已存在并启动成功。
    spawned.clear()
    monkeypatch.setattr(
        setup_wizard,
        "os",
        SimpleNamespace(name="nt", getenv=lambda key, default=None: "production" if key == "PARTYOPS_ENVIRONMENT" else default),
    )
    monkeypatch.setattr(setup_wizard, "_start_windows_host_service", lambda timeout=60.0: None)
    assert setup_wizard.launch_host(config) == "http://127.0.0.1:18765"

    # production 环境：服务未安装 → 给出明确诊断。
    monkeypatch.setattr(
        setup_wizard,
        "_start_windows_host_service",
        lambda timeout=60.0: (_ for _ in ()).throw(
            ValueError("未检测到“党建智办 PartyOps 主机服务”。请重新运行安装器完成服务安装")
        ),
    )
    with pytest.raises(ValueError, match="主机服务"):
        setup_wizard.launch_host(config)


def test_start_windows_host_service_missing_stopped_and_retry(monkeypatch) -> None:
    """服务缺失直接报错；已停止则重试启动并容忍冷启动慢；诊断信息包含 sc 输出。"""

    _original_monotonic = setup_wizard.time.monotonic
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        text = kwargs.get("text", False)
        if "query" in command[1]:
            # 前三次 query 返回“已停止”，之后返回“正在运行”。
            query_count = sum(1 for c in calls if "query" in c[1])
            if query_count <= 3:
                return subprocess.CompletedProcess(
                    command, 0, "STATE: 4  STOPPED\n服务已停止" if not text else "STATE: 4  STOPPED",
                    "",
                )
            return subprocess.CompletedProcess(
                command, 0, "STATE: 4  RUNNING\n服务正在运行" if not text else "STATE: 4  RUNNING",
                "",
            )
        if "start" in command[1]:
            start_count = sum(1 for c in calls if "start" in c[1])
            # 前两次 start 失败（模拟冷启动慢），第三次成功。
            if start_count <= 2:
                return subprocess.CompletedProcess(
                    command, 1053, "服务未及时响应" if not text else "服务未及时响应", "stderr",
                )
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)
    monkeypatch.setattr(setup_wizard.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    # 服务存在且启动成功：不抛错。
    setup_wizard._start_windows_host_service(timeout=30.0)
    def missing_run(command, **kwargs):
        text = kwargs.get("text", False)
        return subprocess.CompletedProcess(
            command, 1060, "The specified service does not exist" if not text else "指定的服务未安装", "",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", missing_run)
    with pytest.raises(ValueError, match="未检测到"):
        setup_wizard._start_windows_host_service(timeout=5.0)

    # 服务一直启动失败：最终给出包含诊断信息的报错。
    calls.clear()
    # 恢复真实单调时钟，避免 while 循环因恒定时钟而无限循环。
    monkeypatch.setattr(setup_wizard.time, "monotonic", _original_monotonic)
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)

    def failing_run(command, **kwargs):
        calls.append(command)
        text = kwargs.get("text", False)
        if "query" in command[1]:
            return subprocess.CompletedProcess(
                command, 0, "STATE: 4  STOPPED" if not text else "STATE: 4  STOPPED", "",
            )
        return subprocess.CompletedProcess(
            command, 1053, "服务未及时响应" if not text else "服务未及时响应", "stderr",
        )

    monkeypatch.setattr(setup_wizard.subprocess, "run", failing_run)
    with pytest.raises(ValueError, match="诊断信息"):
        setup_wizard._start_windows_host_service(timeout=5.0)


def _run_main(monkeypatch, argv: list[str]) -> SystemExit:
    monkeypatch.setattr(sys, "argv", ["PartyOpsWizard", *argv])
    with pytest.raises(SystemExit) as stopped:
        setup_wizard.main()
    return stopped.value


def test_main_dispatches_privileged_shared_root_and_normal_wizard(monkeypatch, tmp_path) -> None:
    original_os = setup_wizard.os
    monkeypatch.setattr(setup_wizard, "os", SimpleNamespace(name="posix", getenv=original_os.getenv))
    denied = _run_main(monkeypatch, ["--privileged-host-config", "--host", "127.0.0.1"])
    assert "需要管理员权限" in str(denied)

    monkeypatch.setattr(setup_wizard, "os", SimpleNamespace(name="nt", getenv=original_os.getenv))
    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    written: list[tuple] = []
    monkeypatch.setattr(setup_wizard, "write_host_config", lambda *args, **kwargs: written.append((args, kwargs)))
    data_dir = tmp_path / "业务数据"
    privileged = _run_main(
        monkeypatch,
        [
            "--privileged-host-config",
            "--host",
            "192.168.8.20",
            "--port",
            "18765",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert privileged.code == 0 and written[0][0][0] == "192.168.8.20"
    assert written[0][0][2] == data_dir
    assert written[0][1]["write_user_mode"] is False

    invalid_uri = _run_main(monkeypatch, ["--manage-shared-roots", "--action-uri", "https://example.test/token"])
    assert "无效的本机共享操作地址" in str(invalid_uri)
    empty_token = _run_main(monkeypatch, ["--manage-shared-roots", "--action-uri", "partyops-client://manage-shares/"])
    assert "令牌无效" in str(empty_token)
    huge_token = _run_main(
        monkeypatch,
        ["--manage-shared-roots", "--action-uri", f"partyops-client://manage-shares/{'x' * 257}"],
    )
    assert "令牌无效" in str(huge_token)

    managed: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        setup_wizard,
        "run_shared_root_manager",
        lambda browser, token: managed.append((browser, token)) or 7,
    )
    valid = _run_main(
        monkeypatch,
        ["--manage-shared-roots", "--no-browser", "--action-uri", f"partyops-client://manage-shares/{'x' * 43}"],
    )
    assert valid.code == 7 and managed == [(False, "x" * 43)]

    launched: list[tuple[bool, str]] = []
    monkeypatch.setattr(
        setup_wizard,
        "run_wizard",
        lambda browser, role: launched.append((browser, role)) or 3,
    )
    normal = _run_main(monkeypatch, ["--no-browser", "--initial-role", "client"])
    assert normal.code == 3 and launched == [(False, "client")]
