"""Windows 桌面入口必须在页面交接失败时给出可见中文诊断。"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


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
    assert opened == ["https://127.0.0.1:18765/devices"]
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
