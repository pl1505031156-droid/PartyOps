"""配置向导事务、诊断与命令行入口的真实回环分支回归。"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

from app import setup_wizard


def _wait_for_url(marker: Path) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            value = marker.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("配置向导没有发布回环地址")


def _request(
    url: str,
    *,
    path: str = "/",
    form: dict[str, str] | None = None,
) -> tuple[int, str]:
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(url + path, data=data)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _start_wizard(
    monkeypatch: pytest.MonkeyPatch,
    config: Path,
    *,
    reconfiguration: bool,
) -> tuple[str, threading.Thread, list[int]]:
    config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config)
    monkeypatch.setattr(
        setup_wizard, "recover_pending_windows_host_switch", lambda: None
    )
    monkeypatch.setattr(setup_wizard.secrets, "token_urlsafe", lambda _size: "csrf-token")
    results: list[int] = []
    thread = threading.Thread(
        target=lambda: results.append(
            setup_wizard.run_wizard(False, reconfiguration=reconfiguration)
        ),
        daemon=True,
    )
    thread.start()
    return _wait_for_url(config / "wizard.url"), thread, results


def _stop_with_client(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    thread: threading.Thread,
    results: list[int],
    tmp_path: Path,
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    pending = tmp_path / "config" / "pending-enrollment.json"
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda value: (value, {"status": "ok", "app_version": "1.4.5-rc.3"}),
    )
    monkeypatch.setattr(
        setup_wizard,
        "enroll_device",
        lambda *_args, **_kwargs: {"device_id": "device-1", "device_token": "token"},
    )
    monkeypatch.setattr(setup_wizard, "write_device_config", lambda *_args, **_kwargs: pending)
    monkeypatch.setattr(setup_wizard, "launch_client", lambda _path: "https://host/device")
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda _url: True)
    status, body = _request(
        url,
        form={
            "csrf": "csrf-token",
            "mode": "client",
            "host_url": "https://host",
            "token": "enrollment-token",
            "device_name": "协同终端",
            "shared_dir": str(shared),
            "backup_dir": str(tmp_path / "backup"),
        },
    )
    assert status == 200 and "协同终端已启动" in body
    thread.join(timeout=3)
    assert not thread.is_alive() and results == [0]


def test_wizard_reconfiguration_personal_success_and_missing_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "personal.env"
    config_path.write_text("PARTYOPS_MODE=personal\n", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "write_personal_config", lambda *_args: config_path)
    monkeypatch.setattr(
        setup_wizard, "launch_personal", lambda _path: "http://127.0.0.1:18775"
    )
    monkeypatch.setattr(setup_wizard, "configured_runtime_status", lambda *_args, **_kwargs: True)
    url, thread, results = _start_wizard(
        monkeypatch, tmp_path / "config", reconfiguration=True
    )

    missing_status, missing_body = _request(url, path="/transactions/missing")
    assert missing_status == 404
    assert json.loads(missing_body)["code"] == "RUNTIME_TRANSACTION_NOT_FOUND"

    status, body = _request(
        url,
        form={
            "csrf": "csrf-token",
            "mode": "personal",
            "port": "18775",
            "data_dir": str(tmp_path / "data"),
        },
    )
    assert status == 202
    transaction = json.loads(body)
    deadline = time.monotonic() + 3
    payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        poll_status, poll_body = _request(url, path=transaction["poll_url"])
        payload = json.loads(poll_body)
        if payload.get("status") == "ready":
            assert poll_status == 200
            break
        time.sleep(0.01)
    assert payload["code"] == "RUNTIME_READY"
    assert payload["redirect_url"] == "http://127.0.0.1:18775"
    thread.join(timeout=3)
    assert not thread.is_alive() and results == [0]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            setup_wizard.HostStartupError(
                "PORT_IN_USE", "主机端口已被其他程序占用"
            ),
            "PORT_IN_USE",
        ),
        (ValueError("数据目录不可用"), "RUNTIME_CONFIGURATION_INVALID"),
        (RuntimeError("unexpected private detail"), "RUNTIME_RECONFIGURATION_FAILED"),
    ],
)
def test_wizard_reconfiguration_failure_is_precise_and_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        setup_wizard,
        "write_personal_config",
        lambda *_args: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(setup_wizard, "_record_wizard_failure", lambda _exc: "diag-123")
    url, thread, results = _start_wizard(
        monkeypatch, tmp_path / "config", reconfiguration=True
    )
    status, body = _request(
        url,
        form={
            "csrf": "csrf-token",
            "mode": "personal",
            "port": "18775",
            "data_dir": str(tmp_path / "data"),
        },
    )
    assert status == 202
    poll_url = json.loads(body)["poll_url"]
    deadline = time.monotonic() + 3
    payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        _, poll_body = _request(url, path=poll_url)
        payload = json.loads(poll_body)
        if payload.get("status") == "failed":
            break
        time.sleep(0.01)
    assert payload["code"] == expected_code
    if expected_code == "RUNTIME_RECONFIGURATION_FAILED":
        assert "diag-123" in str(payload["message"])
        assert "unexpected private detail" not in str(payload["message"])
    _stop_with_client(monkeypatch, url, thread, results, tmp_path)


def test_wizard_host_tls_transaction_and_status_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_config = tmp_path / "partyops.env"
    host_config.write_text(
        f"PARTYOPS_TLS_ENABLED=true\nPARTYOPS_DATA_DIR={tmp_path / 'host-data'}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_wizard, "configure_host_config", lambda *_args: host_config)
    monkeypatch.setattr(
        setup_wizard, "launch_host", lambda _path: "http://192.168.10.2:18765"
    )
    monkeypatch.setattr(
        setup_wizard,
        "configured_runtime_status",
        lambda url, **kwargs: url == "https://192.168.10.2:18765"
        and kwargs["ca_file"].name == "ca.pem",
    )
    url, thread, results = _start_wizard(
        monkeypatch, tmp_path / "config", reconfiguration=True
    )
    status, body = _request(
        url,
        form={
            "csrf": "csrf-token",
            "mode": "host",
            "host": "192.168.10.2",
            "port": "18765",
            "data_dir": str(tmp_path / "host-data"),
        },
    )
    assert status == 202
    poll_url = json.loads(body)["poll_url"]
    deadline = time.monotonic() + 3
    payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        _, poll_body = _request(url, path=poll_url)
        payload = json.loads(poll_body)
        if payload.get("status") == "ready":
            break
        time.sleep(0.01)
    assert payload["redirect_url"] == "https://192.168.10.2:18765"
    thread.join(timeout=3)
    assert not thread.is_alive() and results == [0]


def test_wizard_non_transaction_actions_errors_and_client_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda value: (
            value,
            {"status": "ok", "app_version": "1.4.5-rc.3", "mode": "host"},
        ),
    )
    monkeypatch.setattr(setup_wizard, "_choose_system_folder", lambda: tmp_path / "data")
    monkeypatch.setattr(setup_wizard, "read_service_status", lambda _path: {"stage": "ready", "code": "READY"})
    url, thread, results = _start_wizard(
        monkeypatch, tmp_path / "config", reconfiguration=False
    )

    status, body = _request(url)
    assert status == 200 and "csrf-token" in body
    status, body = _request(
        url,
        form={"csrf": "csrf-token", "mode": "check_client", "host_url": "https://host"},
    )
    assert status == 200 and json.loads(body)["app_version"] == "1.4.5-rc.3"
    status, body = _request(
        url, form={"csrf": "csrf-token", "mode": "browse_data_dir"}
    )
    assert status == 200 and json.loads(body)["path"].endswith("data")
    status, body = _request(url, form={"csrf": "csrf-token", "mode": "host_status"})
    assert status == 200 and json.loads(body)["ui_stage"] == "ready"

    status, body = _request(url, form={"csrf": "wrong", "mode": "personal"})
    assert status == 400 and "配置页面已失效" in body
    status, body = _request(
        url,
        form={
            "csrf": "csrf-token",
            "mode": "client",
            "host_url": "https://host",
            "device_name": "",
        },
    )
    assert status == 400 and "必须填写本机设备名称" in body
    _stop_with_client(monkeypatch, url, thread, results, tmp_path)


def test_setup_wizard_main_privilege_and_protocol_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(*arguments: str) -> str:
        monkeypatch.setattr(sys, "argv", ["partyops-wizard", *arguments])
        with pytest.raises(SystemExit) as raised:
            setup_wizard.main()
        return str(raised.value)

    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: False)
    assert "管理员权限" in run("--privileged-disable-host")
    assert "管理员权限" in run("--privileged-restore-host")
    assert "管理员权限" in run("--privileged-finalize-host-switch")
    assert "管理员权限" in run("--privileged-host-config")

    monkeypatch.setattr(setup_wizard, "windows_is_admin", lambda: True)
    monkeypatch.setattr(setup_wizard.os, "name", "nt")
    assert "缺少 --data-dir" in run("--privileged-host-config")
    assert "无效的公文排版事务地址" in run(
        "--manage-shared-roots",
        "--action-uri",
        "partyops-client://official-format/not-a-uuid?unsafe=1",
    )
    assert "公文排版事务标识无效" in run(
        "--manage-shared-roots",
        "--action-uri",
        "partyops-client://official-format/not-a-uuid",
    )
    valid_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.official_format.run_official_format_tool",
        lambda transaction_id, **_kwargs: 7 if transaction_id == valid_id else 9,
    )
    assert run(
        "--manage-shared-roots",
        "--action-uri",
        f"partyops-client://official-format/{valid_id}",
    ) == "7"
    assert "无效的重新配置地址" in run(
        "--manage-shared-roots",
        "--action-uri",
        "partyops-client://reconfigure/path",
    )
    assert "无效的本机共享操作地址" in run(
        "--manage-shared-roots",
        "--action-uri",
        "https://example.invalid/manage-shares/token",
    )
    assert "本机共享操作令牌无效" in run(
        "--manage-shared-roots",
        "--action-uri",
        "partyops-client://manage-shares/short",
    )
