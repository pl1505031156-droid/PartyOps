"""通过本机 HTTP 界面真实驱动首次配置和共享目录管理工具。"""

from __future__ import annotations

import http.client
import json
import queue
import re
import threading
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import setup_wizard


def _request(url: str, method: str = "GET", form: dict[str, str] | None = None):
    parsed = urllib.parse.urlparse(url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = urllib.parse.urlencode(form or {}).encode("utf-8") if form is not None else None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
    connection.request(method, parsed.path or "/", body=body, headers=headers)
    response = connection.getresponse()
    payload = response.read().decode("utf-8")
    result = (response.status, dict(response.getheaders()), payload)
    connection.close()
    return result


def _csrf(page: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match
    return match.group(1)


def _start_local_tool(monkeypatch: pytest.MonkeyPatch, target):
    opened: queue.Queue[str] = queue.Queue()
    results: list[object] = []
    monkeypatch.setattr(
        setup_wizard.webbrowser,
        "open",
        lambda url: opened.put(str(url)) or True,
    )
    thread = threading.Thread(target=lambda: results.append(target()), daemon=True)
    thread.start()
    return opened.get(timeout=5), thread, results, opened


def test_shared_root_manager_real_http_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "config"
    config_root.mkdir()
    shared = tmp_path / "共享目录"
    shared.mkdir()
    config_path = config_root / "client.json"
    config: dict[str, object] = {
        "host_url": "https://192.168.8.20:18765",
        "device_token": "device-token",
        "backup_dir": str(tmp_path / "backup"),
        "shared_roots": [
            {
                "root_id": "root-1",
                "name": "原共享目录",
                "local_path": str(shared),
                "approval_status": "approved",
            }
        ],
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: config_root)
    monkeypatch.setattr(setup_wizard, "configure_ssl_context", lambda _config: None)
    monkeypatch.setattr(
        setup_wizard,
        "validate_config",
        lambda _config: ("https://192.168.8.20:18765", "device-token", tmp_path / "backup"),
    )
    calls: list[tuple] = []

    def roots(_host, _token, current, _path):
        return list(current.get("shared_roots", []))

    def add(_host, _token, current, _path, selected, name, action_token):
        item = {
            "root_id": "root-2",
            "name": name or selected.name,
            "local_path": str(selected),
            "approval_status": "approved",
        }
        current.setdefault("shared_roots", []).append(item)
        calls.append(("add", str(selected), action_token))
        return item

    monkeypatch.setattr(setup_wizard, "refresh_shared_root_statuses", roots)
    monkeypatch.setattr(setup_wizard, "add_shared_root", add)
    monkeypatch.setattr(
        setup_wizard,
        "rename_shared_root",
        lambda *_args: calls.append(("rename", _args[-2], _args[-1])),
    )
    monkeypatch.setattr(
        setup_wizard,
        "remove_shared_root",
        lambda *_args: calls.append(("remove", _args[-1])),
    )
    monkeypatch.setattr(
        setup_wizard,
        "scan_and_upload_roots",
        lambda *_args: calls.append(("sync",)) or (7, 1),
    )

    url, thread, results, _opened = _start_local_tool(
        monkeypatch,
        lambda: setup_wizard.run_shared_root_manager(True, "single-use-token"),
    )
    status, _headers, page = _request(url)
    assert status == 200 and "原共享目录" in page
    csrf = _csrf(page)
    status, _headers, body = _request(
        url, "POST", {"csrf": "wrong", "action": "sync"}
    )
    assert status == 400 and "页面已失效" in body

    status, _headers, body = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "action": "add",
            "local_path": str(shared),
            "name": "新增共享",
        },
    )
    assert status == 200 and "已发布" in body
    assert calls[-1] == ("add", str(shared), "single-use-token")
    status, _headers, body = _request(
        url,
        "POST",
        {"csrf": csrf, "action": "rename", "root_id": "root-1", "name": "重命名共享"},
    )
    assert status == 200 and "名称已更新" in body
    status, _headers, body = _request(
        url, "POST", {"csrf": csrf, "action": "sync"}
    )
    assert status == 200 and "共登记 7 个文件或目录" in body
    status, _headers, body = _request(
        url, "POST", {"csrf": csrf, "action": "remove", "root_id": "root-1"}
    )
    assert status == 200 and "立即隐藏" in body
    status, _headers, body = _request(
        url, "POST", {"csrf": csrf, "action": "unknown"}
    )
    assert status == 400 and "未知的共享目录操作" in body
    assert _request(url, "POST", {"csrf": csrf, "action": "close"})[0] == 200
    thread.join(timeout=5)
    assert not thread.is_alive() and results == [0]


def test_first_run_host_http_flow_keeps_admin_creation_in_wizard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = tmp_path / "partyops.env"
    config.write_text(
        "PARTYOPS_HOST=192.168.8.20\nPARTYOPS_PORT=18765\nPARTYOPS_TLS_ENABLED=false\n",
        encoding="utf-8",
    )
    configured: list[tuple] = []
    bootstrapped: list[tuple] = []
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: ["192.168.8.20"])
    monkeypatch.setattr(
        setup_wizard,
        "configure_host_config",
        lambda host, port, data: configured.append((host, port, data)) or config,
    )
    monkeypatch.setattr(setup_wizard, "launch_host", lambda _path: "http://192.168.8.20:18765")
    monkeypatch.setattr(
        setup_wizard,
        "ensure_configured_runtime_ready",
        lambda _path, _mode: "http://192.168.8.20:18765",
    )
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda url, token=None: (url.replace("http://", "https://"), {"status": "ok", "mode": "host", "app_version": "1.4.2"}),
    )
    monkeypatch.setattr(
        setup_wizard,
        "bootstrap_first_admin",
        lambda service_url, **values: bootstrapped.append((service_url, values)),
    )
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)

    url, thread, results, _opened = _start_local_tool(
        monkeypatch, lambda: setup_wizard.run_wizard(True, "host")
    )
    status, _headers, page = _request(url)
    assert status == 200 and "配置主机" in page
    csrf = _csrf(page)
    check = _request(
        url,
        "POST",
        {"csrf": csrf, "mode": "check_client", "host_url": "192.168.8.20:18765"},
    )
    assert check[0] == 200 and json.loads(check[2])["status"] == "ok"
    host = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "host",
            "host": "192.168.8.20",
            "port": "18765",
            "data_dir": str(tmp_path / "data"),
        },
    )
    assert host[0] == 200 and "首次配置最后一步" in host[2]
    assert configured[0][0:2] == ("192.168.8.20", 18765)
    created = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "bootstrap_admin",
            "username": "Admin_01",
            "display_name": "首位管理员",
            "password": "PartyOps@2026",
        },
    )
    assert created[0] == 303
    # 跳转协议由刚写入的主机配置决定；本用例明确关闭 TLS。
    assert created[1]["Location"] == "http://192.168.8.20:18765"
    thread.join(timeout=5)
    assert not thread.is_alive() and results == [0]
    assert bootstrapped[0][0] == "http://192.168.8.20:18765"


def test_first_run_client_http_flow_validates_before_consuming_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shared = tmp_path / "共享目录"
    shared.mkdir()
    config = tmp_path / "client.json"
    enrollment = {"device_id": "device-1", "device_token": "device-token"}
    calls: list[tuple] = []
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: ["192.168.8.20"])
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda url, token=None: ("https://192.168.8.20:18765", {"status": "ok", "mode": "host"}),
    )
    monkeypatch.setattr(
        setup_wizard,
        "enroll_device",
        lambda host, token, name, **kwargs: calls.append(("enroll", host, token, name, kwargs["pending_path"])) or enrollment,
    )
    monkeypatch.setattr(
        setup_wizard,
        "write_device_config",
        lambda host, enrolled, backup, **kwargs: calls.append(("write", host, backup, kwargs)) or config,
    )
    monkeypatch.setattr(setup_wizard, "launch_client", lambda _path: "https://192.168.8.20:18765/device-ready")
    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path / "config")
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)

    url, thread, results, opened = _start_local_tool(
        monkeypatch, lambda: setup_wizard.run_wizard(True, "client")
    )
    page = _request(url)[2]
    csrf = _csrf(page)
    invalid = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "client",
            "host_url": "192.168.8.20:18765",
            "device_name": "",
            "token": "enrollment-code",
            "backup_dir": str(tmp_path / "backup"),
            "shared_dir": str(shared),
        },
    )
    assert invalid[0] == 400 and "必须填写本机设备名称" in invalid[2]
    assert not calls
    valid = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "client",
            "host_url": "192.168.8.20:18765",
            "device_name": "档案室协同机",
            "token": "enrollment-code",
            "backup_dir": str(tmp_path / "backup"),
            "shared_dir": str(shared),
        },
    )
    assert valid[0] == 200 and "协同终端已启动并连接" in valid[2]
    thread.join(timeout=5)
    assert not thread.is_alive() and results == [0]
    assert calls[0][0] == "enroll" and calls[1][0] == "write"
    assert opened.get(timeout=2) == "https://192.168.8.20:18765/device-ready"


def test_first_run_personal_http_diagnostics_errors_and_admin_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """新手向导所有辅助动作都返回中文结果，失败后可在原页直接重试。"""

    program_data = tmp_path / "ProgramData"
    config_path = program_data / "PartyOps" / "partyops.env"
    config_path.parent.mkdir(parents=True)
    data_dir = tmp_path / "个人 数据"
    config_path.write_text(
        f"PARTYOPS_DATA_DIR={data_dir}\nPARTYOPS_PORT=18775\n",
        encoding="utf-8",
    )
    personal_config = tmp_path / "personal.env"
    personal_config.write_text(
        f"PARTYOPS_DATA_DIR={data_dir}\nPARTYOPS_PORT=18775\nPARTYOPS_BOOTSTRAP_TOKEN=bootstrap\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    monkeypatch.setattr(setup_wizard, "_choose_system_folder", lambda: data_dir)
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda *_args: (_ for _ in ()).throw(ValueError("主机地址不可用")),
    )
    statuses = iter(
        [
            {"stage": "preparing"},
            {"stage": "child_running"},
            {"stage": "child_exited", "code": "CHILD_EXITED"},
            {"stage": "ready"},
        ]
    )
    monkeypatch.setattr(setup_wizard, "read_service_status", lambda _path: next(statuses))
    monkeypatch.setattr(setup_wizard, "service_log_path", lambda root: root / "logs" / "service.log")
    monkeypatch.setattr(
        setup_wizard,
        "socket",
        SimpleNamespace(
            create_connection=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("not-listening")
            )
        ),
    )
    opened_logs: list[Path] = []
    monkeypatch.setattr(setup_wizard.os, "startfile", lambda path: opened_logs.append(Path(path)), raising=False)
    monkeypatch.setattr(setup_wizard, "write_personal_config", lambda *_args: personal_config)
    monkeypatch.setattr(setup_wizard, "launch_personal", lambda _path: "http://127.0.0.1:18775")
    monkeypatch.setattr(
        setup_wizard,
        "load_host_environment",
        lambda path: (
            {
                "PARTYOPS_DATA_DIR": str(data_dir),
                "PARTYOPS_PORT": "18775",
                "PARTYOPS_BOOTSTRAP_TOKEN": "bootstrap",
            }
            if path in {config_path, personal_config}
            else {}
        ),
    )
    attempts: list[str] = []

    def bootstrap(_url: str, **_values) -> None:
        attempts.append(_values["username"])
        if len(attempts) == 1:
            raise ValueError("密码强度不足，请修改后重试")

    monkeypatch.setattr(setup_wizard, "bootstrap_first_admin", bootstrap)
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)

    url, thread, results, _opened = _start_local_tool(
        monkeypatch,
        lambda: setup_wizard.run_wizard(True, "personal"),
    )
    page = _request(url)[2]
    csrf = _csrf(page)
    assert _request(url, "POST", {"csrf": "wrong", "mode": "personal"})[0] == 400
    browse = _request(url, "POST", {"csrf": csrf, "mode": "browse_data_dir"})
    assert json.loads(browse[2])["path"] == str(data_dir)
    check = _request(url, "POST", {"csrf": csrf, "mode": "check_client", "host_url": "bad"})
    assert check[0] == 400 and "主机地址不可用" in check[2]
    for expected in ("service", "child"):
        status = _request(url, "POST", {"csrf": csrf, "mode": "host_status"})
        assert json.loads(status[2])["ui_stage"] == expected
    exited = _request(url, "POST", {"csrf": csrf, "mode": "host_status"})
    assert json.loads(exited[2])["code"] == "CHILD_EXITED"
    ready = _request(url, "POST", {"csrf": csrf, "mode": "host_status"})
    assert json.loads(ready[2])["ui_stage"] == "ready"
    opened = _request(url, "POST", {"csrf": csrf, "mode": "open_host_logs"})
    assert json.loads(opened[2])["opened"] is True and opened_logs
    assert _request(url, "POST", {"csrf": csrf, "mode": "unknown"})[0] == 400

    configured = _request(
        url,
        "POST",
        {"csrf": csrf, "mode": "personal", "port": "18775", "data_dir": str(data_dir)},
    )
    assert configured[0] == 200 and "创建首位管理员" in configured[2]
    first = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "bootstrap_admin",
            "username": "admin",
            "display_name": "管理员",
            "password": "weak",
        },
    )
    assert first[0] == 400 and "密码强度不足" in first[2]
    second = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "bootstrap_admin",
            "username": "admin",
            "display_name": "管理员",
            "password": "PartyOps@2026",
        },
    )
    assert second[0] == 303 and second[1]["Location"] == "http://127.0.0.1:18775"
    thread.join(timeout=5)
    assert not thread.is_alive() and results == [0]
    assert attempts == ["admin", "admin"]


def test_reconfiguration_returns_same_origin_transaction_before_navigation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "personal.env"
    config_path.write_text("PARTYOPS_PORT=18775\n", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "write_personal_config", lambda *_args: config_path)
    monkeypatch.setattr(
        setup_wizard,
        "launch_personal",
        lambda _path: "http://127.0.0.1:18775",
    )
    monkeypatch.setattr(
        setup_wizard,
        "configured_runtime_status",
        lambda *_args, **_kwargs: True,
    )

    url, thread, results, _opened = _start_local_tool(
        monkeypatch,
        lambda: setup_wizard.run_wizard(
            True,
            "personal",
            reconfiguration=True,
        ),
    )
    page = _request(url)[2]
    assert "pollRuntimeTransaction" in page
    assert "redirect:'manual'" in page
    csrf = _csrf(page)
    accepted = _request(
        url,
        "POST",
        {
            "csrf": csrf,
            "mode": "personal",
            "port": "18775",
            "data_dir": str(tmp_path),
        },
    )
    assert accepted[0] == 202, accepted[2]
    transaction = json.loads(accepted[2])
    poll_url = urllib.parse.urljoin(url, transaction["poll_url"])
    for _attempt in range(20):
        status = _request(poll_url)
        payload = json.loads(status[2])
        if payload["status"] == "ready":
            break
    else:
        raise AssertionError("运行身份事务未进入 ready")
    assert payload["code"] == "RUNTIME_READY"
    assert payload["redirect_url"] == "http://127.0.0.1:18775"
    thread.join(timeout=5)
    assert not thread.is_alive() and results == [0]
