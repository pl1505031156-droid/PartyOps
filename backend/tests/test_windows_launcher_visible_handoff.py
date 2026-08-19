"""Windows 桌面入口必须在页面交接失败时给出可见中文诊断。"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def load_launcher():
    launcher_path = (
        Path(__file__).parents[2] / "packaging" / "windows" / "windows_launcher.py"
    )
    spec = importlib.util.spec_from_file_location(
        "partyops_windows_launcher_handoff_test", launcher_path
    )
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher


def test_browser_shell_failure_is_visible_and_contains_copyable_url(monkeypatch) -> None:
    launcher = load_launcher()
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_launch_failure", shown.append)
    monkeypatch.setattr(
        launcher.os,
        "startfile",
        lambda _url: (_ for _ in ()).throw(OSError("关联损坏")),
        raising=False,
    )
    monkeypatch.setattr(launcher.webbrowser, "open", lambda _url: False)

    assert launcher.open_browser_or_explain("http://127.0.0.1:18765") is False
    assert shown and "系统默认浏览器未能打开" in shown[0]
    assert "http://127.0.0.1:18765" in shown[0]


def test_client_desktop_launch_waits_for_page_marker(monkeypatch, tmp_path: Path) -> None:
    launcher = load_launcher()
    runtime = tmp_path / "runtime"
    local = tmp_path / "config"
    runtime.mkdir()
    local.mkdir()
    config = local / "client.json"
    config.write_text("{}", encoding="utf-8")

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        assert "--no-open-browser" in command
        marker = Path(command[command.index("--browser-url-file") + 1])
        marker.write_text("https://127.0.0.1:18765/devices\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    opened: list[str] = []
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    monkeypatch.setattr(
        launcher,
        "open_browser_or_explain",
        lambda url: opened.append(url) is None,
    )

    assert launcher.prepare_client_page(runtime, config, local) is True
    assert opened == [
        f"https://127.0.0.1:18765/devices?partyops_runtime={launcher.__version__}"
    ]
    assert not (local / "client-browser.url").exists()


def test_client_desktop_launch_timeout_is_visible(monkeypatch, tmp_path: Path) -> None:
    launcher = load_launcher()
    runtime = tmp_path / "runtime"
    local = tmp_path / "config"
    runtime.mkdir()
    local.mkdir()
    config = local / "client.json"
    config.write_text("{}", encoding="utf-8")
    shown: list[str] = []
    monkeypatch.setattr(launcher, "show_launch_failure", shown.append)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("PartyOpsAgent.exe", 120)
        ),
    )

    assert launcher.prepare_client_page(runtime, config, local) is False
    assert shown and "120 秒内未能准备页面" in shown[0]


def test_wizard_desktop_launch_waits_for_marker_and_keeps_diagnostics(
    monkeypatch, tmp_path: Path
) -> None:
    """首次双击必须等待向导真实就绪，不能黑框闪退后丢弃输出。"""

    launcher = load_launcher()
    runtime = tmp_path / "runtime"
    local = tmp_path / "config"
    runtime.mkdir()
    local.mkdir()
    (runtime / "PartyOpsWizard.exe").write_bytes(b"fixture")

    class Process:
        returncode = None

        def poll(self):
            marker = local / "wizard.url"
            marker.write_text("http://127.0.0.1:18790\n", encoding="utf-8")
            return self.returncode

    captured: dict[str, object] = {}

    def popen(command, **kwargs):
        captured["command"] = command
        captured["stdout"] = kwargs.get("stdout")
        captured["stderr"] = kwargs.get("stderr")
        return Process()

    opened: list[str] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", popen)
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher, "_local_tool_reachable", lambda _url: True)
    monkeypatch.setattr(
        launcher,
        "open_browser_or_explain",
        lambda url: opened.append(url) is None,
    )

    assert launcher.launch_wizard_and_wait(runtime, local, ["--initial-role", "personal"])
    assert captured["command"][-3:] == [
        "--no-browser",
        "--initial-role",
        "personal",
    ]
    assert captured["stdout"] is captured["stderr"]
    assert opened == ["http://127.0.0.1:18790"]
    assert (local / "wizard.url").is_file()


def test_windows_gui_entries_are_frozen_without_console() -> None:
    build = (
        Path(__file__).parents[2]
        / "packaging"
        / "windows"
        / "build-windows.ps1"
    ).read_text(encoding="utf-8")

    assert 'Gui = $true' in build
    assert 'if ($entry.Gui) { $arguments += "--noconsole" }' in build

    launcher = (
        Path(__file__).parents[2]
        / "packaging"
        / "windows"
        / "windows_launcher.py"
    ).read_text(encoding="utf-8")
    assert "PERSONAL_CONFIG_INVALID" in launcher
    assert "HOST_CONFIG_INVALID" in launcher
    assert "exc.detail[-1200:]" not in launcher
    assert "client-agent.log" in launcher


def test_business_browser_url_carries_exact_runtime_version() -> None:
    launcher = load_launcher()

    url = launcher.versioned_browser_url(
        "http://127.0.0.1:18765/?page=tasks&partyops_runtime=old"
    )

    assert "page=tasks" in url
    assert url.count("partyops_runtime=") == 1
    assert f"partyops_runtime={launcher.__version__}" in url


def test_unhandled_gui_entry_error_is_visible_and_logged(
    monkeypatch, tmp_path: Path
) -> None:
    launcher = load_launcher()
    shown: list[str] = []
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(launcher, "show_launch_failure", shown.append)
    monkeypatch.setattr(
        launcher,
        "main",
        lambda: (_ for _ in ()).throw(RuntimeError("fault-injection")),
    )

    assert launcher.run_entrypoint_safely() == 2
    emergency_log = tmp_path / "PartyOps-launcher-emergency.log"
    assert emergency_log.is_file()
    assert "fault-injection" in emergency_log.read_text(encoding="utf-8")
    assert shown and "LAUNCHER_UNHANDLED_ERROR" in shown[0]


def test_windows_wizard_wait_allows_slow_legacy_startup() -> None:
    launcher = load_launcher()

    assert launcher.WIZARD_WAIT_SECONDS == 180.0


@pytest.mark.parametrize(
    ("payload", "accepted"),
    [
        ("http://127.0.0.1:18790/\n", True),
        ("http://127.0.0.1:0/\n", False),
        ("http://127.0.0.1:65536/\n", False),
        ("http://127.0.0.1:not-a-port/\n", False),
        ("http://[::1:18790/\n", False),
        ("http://user@127.0.0.1:18790/\n", False),
        ("http://localhost:18790/\n", False),
        ("http://127.0.0.1:18790/?token=secret\n", False),
        ("http://127.0.0.1:18790/#fragment\n", False),
        ("http://127.0.0.1:18790/\nhttp://127.0.0.1:18791/\n", False),
        ("http://127.0.0.1:18790/" + "x" * 2048, False),
    ],
)
def test_wizard_marker_rejects_malformed_or_untrusted_urls(
    tmp_path: Path, payload: str, accepted: bool
) -> None:
    launcher = load_launcher()
    marker = tmp_path / "wizard.url"
    marker.write_text(payload, encoding="utf-8")

    result = launcher.read_loopback_tool_url(marker)

    assert bool(result) is accepted


def test_wizard_marker_rejects_symbolic_link(tmp_path: Path) -> None:
    launcher = load_launcher()
    target = tmp_path / "real.url"
    target.write_text("http://127.0.0.1:18790/\n", encoding="utf-8")
    marker = tmp_path / "wizard.url"
    try:
        marker.symlink_to(target)
    except OSError:
        pytest.skip("当前 Windows 策略不允许普通用户创建符号链接")

    assert launcher.read_loopback_tool_url(marker) == ""


def test_client_handoff_adds_runtime_fingerprint(monkeypatch, tmp_path: Path) -> None:
    launcher = load_launcher()
    runtime = tmp_path / "runtime"
    local = tmp_path / "local"
    config = local / "client.json"
    runtime.mkdir()
    local.mkdir()
    config.write_text("{}", encoding="utf-8")
    (runtime / "PartyOpsAgent.exe").write_bytes(b"fixture")

    def run(*_args, **_kwargs):
        (local / "client-browser.url").write_text(
            "http://127.0.0.1:18766/?page=inbox\n", encoding="utf-8"
        )
        return subprocess.CompletedProcess([], 0)

    opened: list[str] = []
    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(
        launcher, "open_browser_or_explain", lambda url: opened.append(url) is None
    )

    assert launcher.prepare_client_page(runtime, config, local)
    assert opened and "page=inbox" in opened[0]
    assert f"partyops_runtime={launcher.__version__}" in opened[0]
