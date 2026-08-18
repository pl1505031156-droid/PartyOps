"""首次配置向导的跨平台、失败恢复与新手关键路径回归。"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard


def _local_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "config"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setup_wizard, "config_root", lambda: root)
    return root


def test_linux_desktop_tool_marker_and_personal_autostart_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """首次配置与个人模式重登都必须把真实入口交给桌面启动器。"""

    root = _local_config(monkeypatch, tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: runtime)

    url = "http://127.0.0.1:18791"
    marker = setup_wizard._publish_linux_desktop_tool_url("wizard", url)
    assert marker == root / "wizard.url"
    assert marker.read_text(encoding="utf-8") == url + "\n"
    setup_wizard._clear_linux_desktop_tool_url(marker, "http://127.0.0.1:18792")
    assert marker.exists()
    setup_wizard._clear_linux_desktop_tool_url(marker, url)
    assert not marker.exists()
    with pytest.raises(ValueError, match="127.0.0.1"):
        setup_wizard._publish_linux_desktop_tool_url(
            "wizard", "http://192.168.8.20:18791"
        )

    config_path = root / "personal.env"
    config_path.write_text("PARTYOPS_MODE=personal\n", encoding="utf-8")
    autostart = setup_wizard.install_host_autostart(config_path)
    assert autostart is not None
    content = autostart.read_text(encoding="utf-8")
    assert "Exec=env PARTYOPS_ENV_FILE=" in content
    assert str(config_path.resolve()) in content
    assert str((runtime / "start.sh").resolve()).replace("\\", "\\\\") in content


def test_host_and_client_config_are_validated_and_written_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _local_config(monkeypatch, tmp_path)
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "test")
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: ["192.168.8.20"])

    with pytest.raises(ValueError, match="host、personal 或 client"):
        setup_wizard.write_mode_config("server")
    with pytest.raises(ValueError, match="明确局域网地址"):
        setup_wizard.validate_host_config_selection("192.168.8.99", 18765)
    with pytest.raises(ValueError, match="1024"):
        setup_wizard.validate_host_config_selection("192.168.8.20", 80)

    host = setup_wizard.write_host_config(
        "192.168.8.20", 18765, tmp_path / "data"
    )
    environment = setup_wizard.load_host_environment(host)
    assert environment["PARTYOPS_MODE"] == "host"
    assert environment["PARTYOPS_AGENT_PORT"] == "18766"
    assert environment["PARTYOPS_STRICT_SQLITE"] == "true"
    assert json.loads((root / "mode.json").read_text(encoding="utf-8"))["mode"] == "host"

    with pytest.raises(ValueError, match="60 秒"):
        setup_wizard.write_client_config(
            "https://192.168.8.20:18765", "token", tmp_path / "backup", 30
        )
    client = setup_wizard.write_client_config(
        "https://192.168.8.20:18765", "token", tmp_path / "backup", 600
    )
    payload = json.loads(client.read_text(encoding="utf-8"))
    assert payload["mode"] == "client"
    assert payload["host_url"] == "https://192.168.8.20:18765"
    assert payload["updates_dir"] == str((tmp_path / "backup" / "updates").resolve())
    assert not client.with_suffix(".json.tmp").exists()


def test_device_config_preserves_identity_certificates_and_real_share(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _local_config(monkeypatch, tmp_path)
    shared = tmp_path / "共享资料"
    shared.mkdir()
    installed: list[Path] = []
    monkeypatch.setattr(setup_wizard, "install_internal_ca", installed.append)
    monkeypatch.setattr(
        setup_wizard,
        "device_metadata",
        lambda: {"architecture": "amd64", "platform": "windows"},
    )

    with pytest.raises(ValueError, match="完整设备凭据"):
        setup_wizard.write_device_config(
            "https://192.168.8.20:18765",
            {"device_id": "device-1"},
            tmp_path / "backup",
            device_name="协同机",
        )
    with pytest.raises(ValueError, match="60 秒"):
        setup_wizard.write_device_config(
            "https://192.168.8.20:18765",
            {"device_id": "device-1", "device_token": "secret"},
            tmp_path / "backup",
            device_name="协同机",
            interval_seconds=10,
        )

    path = setup_wizard.write_device_config(
        "https://192.168.8.20:18765",
        {
            "device_id": "device-1",
            "device_token": "secret",
            "agent_url": "https://192.168.8.20:18766",
            "_private_key_pem": "PRIVATE",
            "certificate_pem": "CERT",
            "ca_certificate_pem": "CA",
        },
        tmp_path / "backup",
        device_name="档案室协同机",
        shared_dir=shared,
    )
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["device_name"] == "档案室协同机"
    assert config["shared_roots"][0]["local_path"] == str(shared.resolve())
    assert config["updates_dir"] == str((tmp_path / "backup" / "updates").resolve())
    assert (root / "pki" / "device.key").read_text(encoding="utf-8") == "PRIVATE"
    assert installed == [root / "pki" / "ca.pem"]


def test_runtime_helpers_autostart_and_ca_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _local_config(monkeypatch, tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    client_binary = runtime / "partyops-client"
    client_binary.write_bytes(b"binary")
    start_script = runtime / "start.sh"
    start_script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: runtime)
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")

    assert setup_wizard._executable("partyops-client") == client_binary
    with pytest.raises(FileNotFoundError, match="未找到运行程序"):
        setup_wizard._executable("missing")
    host_autostart = setup_wizard.install_host_autostart(root / "partyops.env")
    client_autostart = setup_wizard.install_client_autostart(root / "client.json")
    assert host_autostart and "党建智办主机服务" in host_autostart.read_text(encoding="utf-8")
    assert client_autostart and "--no-open-browser" in client_autostart.read_text(encoding="utf-8")

    ca = tmp_path / "ca.pem"
    ca.write_text("CA", encoding="utf-8")
    helper = runtime / "install-internal-ca.sh"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "denied"),
    )
    with pytest.raises(ValueError, match="证书安装未完成"):
        setup_wizard.install_internal_ca(ca)

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("pkexec", 120)),
    )
    with pytest.raises(ValueError, match="PolicyKit"):
        setup_wizard.install_internal_ca(ca)


def test_launch_client_requires_live_agent_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "https://192.168.8.20:18765",
                "agent_url": "https://192.168.8.20:18766",
                "device_token": "secret",
                "backup_dir": str(tmp_path / "backup"),
            }
        ),
        encoding="utf-8",
    )
    binary = tmp_path / "partyops-client"
    binary.write_bytes(b"agent")
    monkeypatch.setattr(setup_wizard, "configure_ssl_context", lambda _config: None)
    monkeypatch.setattr(setup_wizard, "install_client_autostart", lambda _path: None)
    monkeypatch.setattr(setup_wizard, "_executable", lambda _name: binary)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_spawn",
        lambda command, _log, _env=None: spawned.append(command),
    )
    monkeypatch.setattr(
        setup_wizard,
        "create_browser_launch_url",
        lambda host, agent, token: f"{host}/device-ready?agent={agent}&token={token}",
    )
    attempts = iter([False, False, True])
    monkeypatch.setattr(
        setup_wizard,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: next(attempts),
    )
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    url = setup_wizard.launch_client(config_path)
    assert "device-ready" in url
    assert spawned[0][-1] == "--no-open-browser"

    monkeypatch.setattr(
        setup_wizard, "send_device_heartbeat", lambda *_args, **_kwargs: False
    )
    with pytest.raises(ValueError, match="设备端口 18766"):
        setup_wizard.launch_client(config_path)


def test_host_probe_never_sends_pairing_token_and_falls_back_to_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[tuple[str, object]] = []

    class Response:
        def __init__(self, payload: object):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(self.payload).encode("utf-8")

    def open_url(request, **kwargs):
        requested.append((request.full_url, request.headers))
        if request.full_url.startswith("http://"):
            raise urllib.error.URLError("TLS required")
        return Response({"status": "ok", "mode": "host", "app_version": "1.4.2"})

    monkeypatch.setattr(setup_wizard.urllib.request, "urlopen", open_url)
    resolved, health = setup_wizard.resolve_host_url(
        "http://192.168.8.20:18765", "must-not-leak"
    )
    assert resolved == "https://192.168.8.20:18765"
    assert health["status"] == "ok"
    assert all("must-not-leak" not in str(item) for item in requested)

    for invalid in (
        "ftp://192.168.8.20",
        "http://user:pass@192.168.8.20",
        "http://192.168.8.20/path",
    ):
        with pytest.raises(ValueError, match="无账号、无额外路径"):
            setup_wizard.resolve_host_url(invalid)


def test_first_admin_validation_and_problem_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="至少填写 2 个字"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.8.20:18765", username="admin", display_name="A", password="password"
        )
    with pytest.raises(ValueError, match="用户名"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.8.20:18765", username="管理员", display_name="管理员", password="password"
        )
    with pytest.raises(ValueError, match="8—128"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.8.20:18765", username="admin", display_name="管理员", password="short"
        )
    with pytest.raises(ValueError, match="服务地址无效"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.8.20", username="admin", display_name="管理员", password="password"
        )

    problem = urllib.error.HTTPError(
        "https://127.0.0.1:18765/api/v1/bootstrap/host",
        422,
        "bad",
        {},
        io.BytesIO(b'{"detail":"username already exists"}'),
    )
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(problem),
    )
    with pytest.raises(ValueError, match="username already exists"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.8.20:18765",
            username="admin",
            display_name="管理员",
            password="password",
        )


def test_failure_diagnostic_and_shared_root_page_escape_sensitive_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = _local_config(monkeypatch, tmp_path)
    try:
        raise RuntimeError("secret-token")
    except RuntimeError as exc:
        diagnostic_id = setup_wizard._record_wizard_failure(exc)
    log = (root / "wizard-errors.log").read_text(encoding="utf-8")
    assert diagnostic_id in log and "RuntimeError" in log

    page = setup_wizard.render_shared_root_manager(
        "csrf",
        [
            {
                "root_id": "root-1",
                "name": "<script>alert(1)</script>",
                "local_path": str(tmp_path),
                "approval_status": "approved",
                "last_sync_at": "2026-08-11T10:00:00Z",
            }
        ],
        error="<b>失败</b>",
    )
    assert "&lt;script&gt;" in page and "<script>alert(1)</script>" not in page
    assert "&lt;b&gt;失败&lt;/b&gt;" in page
    assert "立即同步全部已批准目录" in page and ">移除<" in page
