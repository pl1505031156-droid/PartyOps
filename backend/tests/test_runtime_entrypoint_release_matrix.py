"""主服务、更新执行器和协同 Agent 入口的正式发布回归。"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import uuid
from types import SimpleNamespace

import pytest

from app import client_agent, main, pki, update_executor


def test_trace_origin_device_launch_and_frontend_directory(monkeypatch, tmp_path) -> None:
    trace_id = str(uuid.uuid4())
    assert main.normalize_trace_id(trace_id) == trace_id
    assert main.normalize_trace_id(trace_id.upper()) == trace_id
    assert main.normalize_trace_id("bad") != "bad"

    request = SimpleNamespace(
        headers={"Origin": "https://user:password@example.test"},
        url=SimpleNamespace(scheme="https", netloc="example.test"),
    )
    assert main._origin_allowed(request) is False
    request.headers = {}
    assert main._origin_allowed(request) is True

    monkeypatch.setattr(main, "verify_device_context_token", lambda *_args, **_kwargs: None)
    response = main.device_launch("x" * 40, SimpleNamespace())
    assert response.status_code == 303 and "device_context_error=1" in response.headers["location"]
    assert response.headers["Referrer-Policy"] == "no-referrer"

    monkeypatch.setattr(main.settings, "frontend_dist", tmp_path / "frontend")
    assert main.frontend_directory() == (tmp_path / "frontend").resolve()


def test_production_origin_guard_rejects_missing_source_and_accepts_same_referer(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "environment", "production")
    request = SimpleNamespace(
        headers={},
        url=SimpleNamespace(scheme="https", netloc="192.168.1.8:18765"),
    )
    assert main._origin_allowed(request) is False
    request.headers = {"Referer": "https://192.168.1.8:18765/setup/step-3"}
    assert main._origin_allowed(request) is True
    request.headers = {"Referer": "https://attacker.invalid/form"}
    assert main._origin_allowed(request) is False


def test_main_run_tls_agent_success_certificate_guard_and_agent_failure(monkeypatch, tmp_path) -> None:
    cert = tmp_path / "server.pem"
    key = tmp_path / "server.key"
    ca = tmp_path / "ca.pem"
    for path in (cert, key, ca):
        path.write_text("test", encoding="utf-8")

    settings = SimpleNamespace(
        host="127.0.0.1",
        network_bind_host="127.0.0.1",
        network_advertise_host="127.0.0.1",
        port=18765,
        agent_port=18766,
        tls_enabled=True,
        tls_cert_file=cert,
        tls_key_file=key,
        tls_client_ca_file=ca,
        tls_require_client_cert=False,
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(pki, "ensure_tls_material", lambda _settings: {})
    import uvicorn

    calls: list[tuple[str, dict]] = []

    class _Config:
        def __init__(self, target, **options):
            self.target = target
            self.options = options

    class _Server:
        def __init__(self, config):
            self.config = config
            self.started = True
            self.should_exit = False

        def run(self):
            self.started = True

    class _Thread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

        def is_alive(self):
            return True

        def join(self, **_kwargs):
            return None

    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)
    monkeypatch.setattr(uvicorn, "run", lambda target, **options: calls.append((target, options)))
    monkeypatch.setattr(main.threading, "Thread", _Thread)
    main.run()
    assert calls[0][1]["ssl_certfile"] == str(cert)
    assert calls[0][1]["ssl_cert_reqs"] == 0

    settings.tls_cert_file = None
    settings.tls_key_file = None
    with pytest.raises(RuntimeError, match="证书和私钥"):
        main.run()

    settings.tls_cert_file = cert
    settings.tls_key_file = key

    class _DeadServer(_Server):
        def __init__(self, config):
            super().__init__(config)
            self.started = False

        def run(self):
            return None

    class _DeadThread(_Thread):
        def is_alive(self):
            return False

    monkeypatch.setattr(uvicorn, "Server", _DeadServer)
    monkeypatch.setattr(main.threading, "Thread", _DeadThread)
    with pytest.raises(RuntimeError, match="安全端口启动失败"):
        main.run()


def _executor_main(monkeypatch, argv: list[str]) -> SystemExit:
    monkeypatch.setattr(sys, "argv", ["partyops-update-executor", *argv])
    with pytest.raises(SystemExit) as stopped:
        update_executor.main()
    return stopped.value


def test_update_executor_main_dispatches_every_operation(monkeypatch, tmp_path) -> None:
    package = tmp_path / "device.partyops-update"
    package.write_bytes(b"package")
    monkeypatch.setattr(update_executor, "install_device_package", lambda path: path == package)
    assert _executor_main(monkeypatch, ["--install-package", str(package)]).code == 0
    monkeypatch.setattr(update_executor, "install_device_package", lambda _path: False)
    assert _executor_main(monkeypatch, ["--install-package", str(package)]).code == 1

    monkeypatch.setattr(update_executor, "run_supervisor", lambda once: 8 if once else 9)
    assert _executor_main(monkeypatch, ["--supervisor", "--once"]).code == 8
    monkeypatch.setattr(update_executor, "execute_host_update", lambda run_id: run_id == "ok")
    assert _executor_main(monkeypatch, ["--run-id", "ok"]).code == 0
    assert _executor_main(monkeypatch, ["--run-id", "failed"]).code == 1
    monkeypatch.setattr(update_executor, "run_daemon", lambda once: 6 if once else 7)
    assert _executor_main(monkeypatch, []).code == 7


def test_update_lock_legacy_invalid_and_write_failure(monkeypatch, tmp_path) -> None:
    lock = tmp_path / "update.lock"
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(update_executor.time, "time", lambda: lock.stat().st_mtime + 1)
    assert not update_executor._update_lock_is_stale(lock)
    lock.write_text("not-json", encoding="utf-8")
    assert not update_executor._update_lock_is_stale(lock)
    lock.write_text(json.dumps({"pid": 1, "boot_id": "previous"}), encoding="utf-8")
    monkeypatch.setattr(update_executor, "_system_boot_id", lambda: "current")
    assert update_executor._update_lock_is_stale(lock)

    lock.unlink()
    real_write = update_executor.os.write
    monkeypatch.setattr(update_executor.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("disk")))
    assert update_executor._acquire_update_lock(lock) is False
    assert not lock.exists()
    monkeypatch.setattr(update_executor.os, "write", real_write)


def test_agent_ssl_metadata_enrollment_errors_and_transfer_status(monkeypatch, tmp_path) -> None:
    ca = tmp_path / "ca.pem"
    cert = tmp_path / "device.pem"
    key = tmp_path / "device.key"
    for path in (ca, cert, key):
        path.write_text("pem", encoding="utf-8")

    loaded: list[tuple[str, str]] = []

    class _Context:
        def load_cert_chain(self, certfile, keyfile):
            loaded.append((certfile, keyfile))

    monkeypatch.setattr(client_agent.ssl, "create_default_context", lambda **_kwargs: _Context())
    client_agent.configure_ssl_context(
        {"ca_file": str(ca), "certificate_file": str(cert), "key_file": str(key)}
    )
    assert loaded == [(str(cert), str(key))]

    monkeypatch.setattr(
        client_agent.shutil,
        "disk_usage",
        lambda _path: (_ for _ in ()).throw(OSError("disk offline")),
    )
    metadata = client_agent.device_metadata()
    assert metadata["disk_free_bytes"] == 0 and metadata["platform"] == "windows"

    invalid_json = urllib.error.HTTPError(
        "https://host/enroll", 422, "bad", {}, io.BytesIO(b"not-json")
    )
    assert "HTTP 422" in str(client_agent.enrollment_http_error(invalid_json))
    duplicate_name = urllib.error.HTTPError(
        "https://host/enroll",
        409,
        "bad",
        {},
        io.BytesIO('{"code":"DEVICE_NAME_EXISTS","detail":"名称已使用"}'.encode("utf-8")),
    )
    assert "名称已使用" in str(client_agent.enrollment_http_error(duplicate_name))

    monkeypatch.setattr(client_agent, "_json_request", lambda *_args, **_kwargs: ["invalid"])
    with pytest.raises(client_agent.AgentCommandError) as invalid_status:
        client_agent.get_transfer_status("https://host", "token", "transfer-1")
    assert invalid_status.value.code == "TRANSFER_INVALID"


def test_agent_upload_and_download_detect_changes_network_and_hash(monkeypatch, tmp_path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    source = shared / "report.txt"
    source.write_bytes(b"content")
    config = {
        "device_id": "device-1",
        "shared_roots": [
            {
                "remote_key": "root-key",
                "local_path": str(shared),
                "approval_status": "approved",
            }
        ],
        "receive_dir": str(tmp_path / "receive"),
    }
    key = "device-1:root-key:report.txt"
    with pytest.raises(client_agent.AgentCommandError) as changed:
        client_agent.upload_transfer(
            "https://host",
            "token",
            {"transfer_id": "t1", "remote_file_key": key, "size_bytes": 999},
            config,
        )
    assert changed.value.code == "SOURCE_CHANGED"

    monkeypatch.setattr(
        client_agent,
        "get_transfer_status",
        lambda *_args: {"completed_chunks": [], "chunk_size": 1024, "total_chunks": 1},
    )
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_args: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    with pytest.raises(client_agent.AgentCommandError) as interrupted:
        client_agent.upload_transfer(
            "https://host",
            "token",
            {"transfer_id": "t1", "remote_file_key": key, "size_bytes": source.stat().st_size},
            config,
        )
    assert interrupted.value.code == "NETWORK_INTERRUPTED" and interrupted.value.retryable

    monkeypatch.setattr(
        client_agent,
        "get_transfer_status",
        lambda *_args: {"chunk_size": 8, "total_chunks": 0, "size_bytes": 1, "sha256": ""},
    )
    with pytest.raises(client_agent.AgentCommandError) as size_error:
        client_agent.download_transfer(
            "https://host", "token", {"transfer_id": "download-1", "name": "file.txt"}, config
        )
    assert size_error.value.code == "TRANSFER_METADATA_INVALID"
