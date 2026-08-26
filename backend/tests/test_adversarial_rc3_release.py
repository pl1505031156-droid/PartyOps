"""rc.3 发布前对抗式审查发现项的回归测试。"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex
from starlette.requests import Request

from app import __version__ as APP_VERSION
from app import client_agent, setup_wizard, update_executor, windows_host_status
from app import main as app_main
from app.models import UpdateRun
from app.routers import updates as updates_router


class _Response:
    status = 200

    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.payload = payload
        self.headers = headers or {}
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_active_host_update_index_matches_sqlalchemy_enum_storage() -> None:
    """数据库互斥必须匹配 SQLAlchemy 实际持久化的 ``APPLYING``。"""

    index = next(
        item
        for item in UpdateRun.__table__.indexes
        if item.name == "uq_update_runs_one_active_host"
    )
    sql = str(CreateIndex(index).compile(dialect=sqlite.dialect()))
    assert "status = 'APPLYING'" in sql
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE update_runs (status TEXT, target_device_id TEXT)")
        connection.execute(sql)
        connection.execute("INSERT INTO update_runs VALUES ('APPLYING', NULL)")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO update_runs VALUES ('APPLYING', NULL)")
        connection.execute("INSERT INTO update_runs VALUES ('APPLYING', 'device-1')")
        connection.execute("INSERT INTO update_runs VALUES ('FAILED', NULL)")
    finally:
        connection.close()


def test_data_directory_instance_lock_blocks_parallel_runtime_and_releases(tmp_path: Path) -> None:
    first = app_main.DataDirectoryInstanceLock(tmp_path)
    second = app_main.DataDirectoryInstanceLock(tmp_path)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="INSTANCE_ALREADY_RUNNING"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_instance_lock_posix_path_and_unexpected_lock_error(
    monkeypatch, tmp_path: Path
) -> None:
    """Linux 使用 flock；非“已占用”错误必须原样抛出，不能误导用户。"""

    proxy = SimpleNamespace(**vars(os))
    proxy.name = "posix"
    monkeypatch.setattr(app_main, "os", proxy)
    calls: list[int] = []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=1,
        LOCK_NB=2,
        LOCK_UN=4,
        flock=lambda _fd, operation: calls.append(operation),
    )
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    lock = app_main.DataDirectoryInstanceLock(tmp_path / "ok")
    lock.acquire()
    assert lock.handle is not None
    lock.release()
    lock.release()
    assert calls == [3, 4]

    def unexpected(_fd, _operation):
        raise OSError(errno.EINVAL, "invalid lock operation")

    fake_fcntl.flock = unexpected
    with pytest.raises(OSError) as raised:
        app_main.DataDirectoryInstanceLock(tmp_path / "error").acquire()
    assert raised.value.errno == errno.EINVAL


def test_lan_address_cache_avoids_repeated_adapter_enumeration(monkeypatch) -> None:
    app_main._lan_address_cache = (0.0, ())
    ticks = iter((100.0, 101.0, 200.0))
    monkeypatch.setattr(app_main.time, "monotonic", lambda: next(ticks))
    discoveries: list[int] = []
    monkeypatch.setattr(
        app_main,
        "discover_lan_addresses",
        lambda: discoveries.append(1) or ["192.168.1.8"],
    )
    assert app_main.cached_lan_addresses() == ("192.168.1.8",)
    assert app_main.cached_lan_addresses() == ("192.168.1.8",)
    assert app_main.cached_lan_addresses() == ("192.168.1.8",)
    assert len(discoveries) == 2


def test_lan_cache_rechecks_after_lock_and_logging_deduplicates_handler(
    monkeypatch, tmp_path: Path
) -> None:
    """并发等待后复用新缓存；重复初始化日志不会重复写同一文件。"""

    app_main._lan_address_cache = (0.0, ())
    monkeypatch.setattr(app_main.time, "monotonic", lambda: 100.0)

    class CacheLock:
        def __enter__(self):
            app_main._lan_address_cache = (100.0, ("192.168.10.8",))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(app_main, "_lan_address_cache_lock", CacheLock())
    monkeypatch.setattr(
        app_main,
        "discover_lan_addresses",
        lambda: (_ for _ in ()).throw(AssertionError("不得重复枚举网卡")),
    )
    assert app_main.cached_lan_addresses() == ("192.168.10.8",)

    logs_dir = tmp_path / "logs"
    monkeypatch.setattr(app_main, "settings", SimpleNamespace(logs_dir=logs_dir))
    target = str((logs_dir / "partyops.log").resolve()).lower()

    class Handler:
        def __init__(self, *_args, **_kwargs):
            self.baseFilename = "new-handler"

        def setFormatter(self, _formatter):
            return None

    class RootLogger:
        handlers = [SimpleNamespace(baseFilename=target)]

        def addHandler(self, _handler):
            raise AssertionError("不得重复添加同一路径的日志处理器")

        def setLevel(self, _level):
            return None

    monkeypatch.setattr(app_main, "TimedRotatingFileHandler", Handler)
    monkeypatch.setattr(
        app_main,
        "logging",
        SimpleNamespace(INFO=20, getLogger=lambda: RootLogger()),
    )
    app_main.configure_logging()


def test_runtime_initialization_upgrade_success_and_diagnostic_rollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        network_bind_host="127.0.0.1",
        network_advertise_host="127.0.0.1",
        environment="production",
        tls_enabled=False,
        data_dir=tmp_path,
    )
    monkeypatch.setattr(app_main, "settings", settings)
    monkeypatch.setattr(app_main, "configure_logging", lambda: None)
    monkeypatch.setattr(app_main, "validate_bind_host", lambda *_a, **_k: None)
    monkeypatch.setattr(app_main, "validate_transport_security", lambda **_k: None)
    monkeypatch.setattr(app_main, "ensure_device_context_secret", lambda _db: None)
    monkeypatch.setattr(app_main, "ensure_current_release", lambda _db: None)
    seeded: list[object] = []
    monkeypatch.setattr(app_main, "seed_templates", lambda _db, admin: seeded.append(admin))
    recorded: list[tuple[object, str]] = []
    monkeypatch.setattr(
        app_main,
        "record_upgrade",
        lambda revision, _backup, status, **_kwargs: recorded.append((revision, status)),
    )
    monkeypatch.setattr(app_main, "upgrade_required", lambda: (True, "0018"))
    monkeypatch.setattr(app_main, "recover_interrupted_upgrade", lambda: None)
    backup = tmp_path / "backup.db"
    monkeypatch.setattr(app_main, "create_pre_upgrade_backup", lambda: backup)
    states: list[str] = []
    monkeypatch.setattr(
        app_main,
        "write_upgrade_transaction_state",
        lambda state, **_kwargs: states.append(state),
    )
    validated: list[tuple[str, Path]] = []
    registered: list[Path] = []
    monkeypatch.setattr(
        app_main,
        "validate_upgrade_postconditions",
        lambda revision, path: validated.append((revision, path)),
    )
    monkeypatch.setattr(
        app_main,
        "register_pre_upgrade_backup",
        lambda path: registered.append(path),
    )

    admin = SimpleNamespace(id="admin")

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _query):
            return admin

        def commit(self):
            return None

    runtime = SimpleNamespace(
        create_schema=lambda: None,
        validate_capabilities=lambda: {"fts5": True},
        session_factory=lambda: Session(),
    )
    monkeypatch.setattr(app_main, "db_runtime", runtime)
    assert app_main._initialize_runtime() == {"fts5": True}
    assert seeded == [admin] and recorded == [("0018", "completed")]
    assert validated == [("0018", backup)] and registered == [backup]
    assert states == ["backup_verified", "migrating", "validating", "completed"]

    restored: list[Path] = []
    monkeypatch.setattr(
        app_main,
        "restore_database_from_upgrade_backup",
        lambda path: restored.append(path),
    )
    monkeypatch.setattr(
        app_main,
        "classify_database_startup_error",
        lambda _exc: ("DB_LOCKED", "数据库被占用"),
    )
    runtime.create_schema = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(
        app_main,
        "record_upgrade",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("journal denied")),
    )
    proxy = SimpleNamespace(**vars(os))
    proxy.name = "nt"
    monkeypatch.setattr(app_main, "os", proxy)
    monkeypatch.setattr(
        windows_host_status,
        "write_service_status",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("status denied")),
    )
    with pytest.raises(RuntimeError, match="DB_LOCKED"):
        app_main._initialize_runtime()
    assert restored == [backup]


def test_lifespan_releases_instance_lock_when_initialization_fails(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []

    class Lock:
        def __init__(self, _path):
            pass

        def acquire(self):
            events.append("acquire")

        def release(self):
            events.append("release")

    monkeypatch.setattr(app_main, "DataDirectoryInstanceLock", Lock)
    monkeypatch.setattr(app_main, "settings", SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(
        app_main,
        "_initialize_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(app_main.db_runtime, "dispose", lambda: events.append("dispose"))

    async def exercise() -> None:
        manager = app_main.lifespan(app_main.app)
        with pytest.raises(RuntimeError, match="startup failed"):
            await manager.__aenter__()

    asyncio.run(exercise())
    assert events == ["acquire", "dispose", "release"]


def test_windows_supervisor_health_uses_partyops_ca(monkeypatch, tmp_path: Path) -> None:
    missing_argument, detail = windows_host_status.probe_loopback_health(
        18765,
        tls=True,
    )
    assert not missing_argument
    assert "缺少 PartyOps 内部 CA" in detail

    missing, detail = windows_host_status.probe_loopback_health(
        18765,
        tls=True,
        ca_file=tmp_path / "missing-ca.pem",
    )
    assert not missing
    assert "尚未生成" in detail

    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test-ca", encoding="utf-8")
    context = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        windows_host_status.ssl,
        "create_default_context",
        lambda **kwargs: captured.setdefault("cafile", kwargs["cafile"]) or context,
    )

    def open_health(request, **kwargs):
        captured["url"] = request.full_url
        captured["context"] = kwargs.get("context")
        return _Response(
            (
                f'{{"status":"ok","mode":"host","app_version":"{APP_VERSION}",'
                '"sqlite":{"safe_version":true,"fts5":true}}'
            ).encode("utf-8")
        )

    monkeypatch.setattr(windows_host_status.urllib.request, "urlopen", open_health)
    healthy, detail = windows_host_status.probe_loopback_health(
        18765,
        tls=True,
        ca_file=ca_file,
    )
    assert healthy and detail == ""
    assert captured["url"] == "https://127.0.0.1:18765/api/v1/health"
    assert captured["cafile"] == str(ca_file.resolve())
    assert captured["context"] is not None
    assert not windows_host_status.health_payload_ready({"status": "ok"})
    assert not windows_host_status.health_payload_ready(
        {
            "status": "ok",
            "mode": "client",
            "app_version": "1.4.3-rc.3",
            "sqlite": {"safe_version": True, "fts5": True},
        }
    )
    assert not windows_host_status.health_payload_ready(
        {
            "status": "ok",
            "mode": "host",
            "app_version": "1.4.3-rc.2",
            "sqlite": {"safe_version": True, "fts5": True},
        },
        expected_version="1.4.3-rc.3",
    )
    personal_payload = {
        "status": "ok",
        "mode": "personal",
        "app_version": APP_VERSION,
        "sqlite": {"safe_version": True, "fts5": True},
    }
    assert windows_host_status.health_payload_ready(
        personal_payload,
        expected_version=APP_VERSION,
        expected_mode="personal",
    )
    assert not windows_host_status.health_payload_ready(personal_payload)
    assert not windows_host_status.health_payload_ready(
        personal_payload,
        expected_mode="client",
    )


def test_personal_health_wait_accepts_real_personal_payload(
    monkeypatch, tmp_path: Path
) -> None:
    """个人模式启动后不能因共用主机健康契约而误等 180 秒。"""

    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            (
                f'{{"status":"ok","mode":"personal","app_version":"{APP_VERSION}",'
                '"sqlite":{"safe_version":true,"fts5":true}}'
            ).encode("utf-8")
        ),
    )

    assert (
        setup_wizard.wait_for_host_health(
            "127.0.0.1",
            18775,
            timeout=5.0,
            data_dir=tmp_path,
            service_managed=False,
        )
        == "http://127.0.0.1:18775"
    )


def test_production_wizard_never_sends_admin_password_without_ca(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    opened: list[str] = []
    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda request, **_kwargs: opened.append(request.full_url) or _Response(b"{}"),
    )
    with pytest.raises(ValueError, match="拒绝发送管理员密码"):
        setup_wizard.bootstrap_first_admin(
            "https://192.168.1.8:18765",
            username="admin",
            display_name="系统管理员",
            password="PartyOps@2026",
            ca_file=tmp_path / "missing.pem",
        )
    assert opened == []


def test_update_health_uses_loopback_and_partyops_ca(monkeypatch, tmp_path: Path) -> None:
    ca_file = tmp_path / "secrets" / "pki" / "ca.pem"
    ca_file.parent.mkdir(parents=True)
    ca_file.write_text("partyops-ca", encoding="utf-8")
    settings = SimpleNamespace(
        tls_enabled=True,
        tls_client_ca_file=None,
        data_dir=tmp_path,
        host="192.168.100.40",
        port=18765,
    )
    captured: dict[str, object] = {}
    context = object()
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    def create_context(**kwargs):
        captured["cafile"] = kwargs["cafile"]
        return context

    monkeypatch.setattr(update_executor.ssl, "create_default_context", create_context)

    def open_health(request, **kwargs):
        captured["url"] = request.full_url
        captured["context"] = kwargs.get("context")
        return _Response(
            (
                f'{{"status":"ok","mode":"host","app_version":"{APP_VERSION}",'
                '"sqlite":{"safe_version":true,"fts5":true}}'
            ).encode("utf-8")
        )

    monkeypatch.setattr(update_executor.urllib.request, "urlopen", open_health)
    assert update_executor._health_check()
    assert captured["url"] == "https://127.0.0.1:18765/api/v1/health"
    assert captured["cafile"] == str(ca_file.resolve())
    assert captured["context"] is context

    ca_file.unlink()
    captured.clear()
    assert not update_executor._health_check()
    assert captured == {}


@pytest.mark.parametrize(
    ("payload", "expected_version"),
    [
        (b"{}", "1.4.3-rc.3"),
        (
            b'{"status":"ok","mode":"client","app_version":"1.4.3-rc.3",'
            b'"sqlite":{"safe_version":true,"fts5":true}}',
            "1.4.3-rc.3",
        ),
        (
            b'{"status":"ok","mode":"host","app_version":"1.4.3-rc.2",'
            b'"sqlite":{"safe_version":true,"fts5":true}}',
            "1.4.3-rc.3",
        ),
        (
            b'{"status":"ok","mode":"host","app_version":"1.4.3-rc.3",'
            b'"sqlite":{"safe_version":false,"fts5":true}}',
            "1.4.3-rc.3",
        ),
        (
            b'{"status":"ok","mode":"host","app_version":"1.4.3-rc.3",'
            b'"sqlite":{"safe_version":true,"fts5":false}}',
            "1.4.3-rc.3",
        ),
    ],
)
def test_update_health_rejects_false_positive_responses(
    monkeypatch,
    tmp_path: Path,
    payload: bytes,
    expected_version: str,
) -> None:
    settings = SimpleNamespace(
        tls_enabled=False,
        tls_client_ca_file=None,
        data_dir=tmp_path,
        port=18765,
    )
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        update_executor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )
    assert not update_executor._health_check(expected_version)


def test_update_health_requires_bounded_utf8_json(monkeypatch, tmp_path: Path) -> None:
    settings = SimpleNamespace(
        tls_enabled=False,
        tls_client_ca_file=None,
        data_dir=tmp_path,
        port=18765,
    )
    monkeypatch.setattr(update_executor, "get_settings", lambda: settings)
    invalid_payloads = (
        b"\xff",
        b"not-json",
        b"[1,2,3]",
        b"x" * (update_executor.MAX_HEALTH_RESPONSE_BYTES + 1),
    )
    for payload in invalid_payloads:
        monkeypatch.setattr(
            update_executor.urllib.request,
            "urlopen",
            lambda *_args, _payload=payload, **_kwargs: _Response(_payload),
        )
        assert not update_executor._health_check("1.4.3-rc.3")


def test_privileged_update_lock_is_outside_custom_data_dir(
    monkeypatch, tmp_path: Path
) -> None:
    custom_data = tmp_path / "用户可写数据"
    program_data = tmp_path / "ProgramData"
    monkeypatch.setenv("PARTYOPS_ENVIRONMENT", "production")
    monkeypatch.setenv("PROGRAMDATA", str(program_data))
    assert update_executor._update_lock_path(custom_data) == (
        program_data / "PartyOps-System" / "update.lock"
    )

    real_parent = tmp_path / "real-lock-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-lock-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    assert not update_executor._acquire_update_lock(linked_parent / "update.lock")


def test_update_artifact_hash_failure_preserves_previous_cache(
    monkeypatch, tmp_path: Path
) -> None:
    artifact_name = "PartyOps_1.4.5_windows_amd64.exe"
    package = tmp_path / "release.partyops-update"
    payload = b"new-installer"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(artifact_name, payload)
    manifest = {
        "version": "1.4.5",
        "artifacts": {
            artifact_name: {
                "size": len(payload),
                "sha256": "0" * 64,
            }
        },
        "platform_artifacts": {"windows": {"amd64": artifact_name}},
    }
    target = tmp_path / "cache" / artifact_name
    target.parent.mkdir()
    target.write_bytes(b"known-good-rollback")
    monkeypatch.setattr(update_executor, "_verify_manifest_signature", lambda _manifest: True)

    with pytest.raises(RuntimeError, match="哈希"):
        update_executor._select_artifact(
            package,
            manifest,
            "amd64",
            target,
            "windows",
        )
    assert target.read_bytes() == b"known-good-rollback"
    assert not list(target.parent.glob("*.verified"))

    manifest["artifacts"][artifact_name]["sha256"] = hashlib.sha256(payload).hexdigest()
    selected = update_executor._select_artifact(
        package,
        manifest,
        "amd64",
        target,
        "windows",
    )
    assert selected == target
    assert target.read_bytes() == payload


def test_rollback_cache_requires_matching_digest_and_updates_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.exe"
    target = tmp_path / "cache" / "current.exe"
    source.write_bytes(b"verified-installer")
    update_executor._cache_verified_rollback_artifact(source, target)
    assert update_executor._verify_cached_rollback_artifact(target)
    assert update_executor._rollback_digest_path(target).read_text(encoding="ascii") == hashlib.sha256(
        b"verified-installer"
    ).hexdigest()

    target.write_bytes(b"tampered")
    assert not update_executor._verify_cached_rollback_artifact(target)
    update_executor._rollback_digest_path(target).write_text("not-a-digest", encoding="ascii")
    assert not update_executor._verify_cached_rollback_artifact(target)


def test_production_rejects_dns_rebinding_host_and_malformed_origin(monkeypatch) -> None:
    monkeypatch.setattr(app_main.settings, "environment", "production")
    monkeypatch.setattr(app_main.settings, "host", "192.168.100.40")
    monkeypatch.setattr(app_main.settings, "bind_host", "0.0.0.0")
    monkeypatch.setattr(app_main.settings, "advertise_host", "192.168.100.40")
    monkeypatch.setattr(app_main, "discover_lan_addresses", lambda: ["192.168.100.40"])

    def request(host: str, origin: str = "") -> Request:
        headers = [(b"host", host.encode("ascii"))]
        if origin:
            headers.append((b"origin", origin.encode("ascii")))
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/logout",
                "query_string": b"",
                "scheme": "https",
                "server": ("192.168.100.40", 18765),
                "client": ("192.168.100.50", 50000),
                "headers": headers,
            }
        )

    assert not app_main._host_allowed(request("attacker.invalid"))
    assert app_main._host_allowed(request("192.168.100.40:18765"))
    assert not app_main._origin_allowed(
        request("192.168.100.40:18765", "https://192.168.100.40:18765/forged")
    )


def test_agent_update_download_limits_and_cleans_untrusted_files(
    monkeypatch, tmp_path: Path
) -> None:
    updates = tmp_path / "updates"
    config = {"updates_dir": str(updates)}
    payload = {"package": "partyops_1.4.3.partyops-update"}
    monkeypatch.setattr(client_agent, "MAX_UPDATE_PACKAGE_BYTES", 3)
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_a, **_k: _Response(b"four", {"Content-Length": "4"}),
    )
    with pytest.raises(client_agent.AgentCommandError) as too_large:
        client_agent.apply_update_command("https://host", "token", payload, config)
    assert too_large.value.code == "UPDATE_PACKAGE_TOO_LARGE"
    assert not list(updates.iterdir())

    monkeypatch.setattr(client_agent, "MAX_UPDATE_PACKAGE_BYTES", 1024)
    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_a, **_k: _Response(b"package", {"Content-Length": "invalid"}),
    )
    with pytest.raises(client_agent.AgentCommandError) as invalid_length:
        client_agent.apply_update_command("https://host", "token", payload, config)
    assert invalid_length.value.code == "UPDATE_PACKAGE_LENGTH_INVALID"
    assert not list(updates.iterdir())

    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_a, **_k: _Response(b"short", {"Content-Length": "9"}),
    )
    with pytest.raises(client_agent.AgentCommandError) as length_mismatch:
        client_agent.apply_update_command("https://host", "token", payload, config)
    assert length_mismatch.value.code == "UPDATE_PACKAGE_LENGTH_MISMATCH"
    assert not list(updates.iterdir())

    monkeypatch.setattr(
        client_agent,
        "_urlopen",
        lambda *_a, **_k: _Response(b"package", {"Content-Length": "7"}),
    )
    result = client_agent.apply_update_command("https://host", "token", payload, config)
    assert result["error_code"] == "UPDATE_HELPER_MISSING"
    assert not list(updates.iterdir())


def test_inno_postinstall_is_transactional_and_conflict_evidence_is_strong() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "windows"
        / "PartyOps.iss"
    ).read_text(encoding="utf-8")
    assert "procedure RollbackPostInstall;" in script
    assert "ErrorMessage := GetExceptionMessage;\n    RollbackPostInstall;\n    RaiseException(ErrorMessage);" in script
    assert "procedure DeinitializeSetup;" in script
    assert "if CurStep = ssDone then" in script
    rollback = script.split("procedure RollbackPostInstall;", 1)[1].split(
        "function GetCustomSetupExitCode", 1
    )[0]
    assert "if UpdateServiceExistedBeforeInstall then\n      Exec(UpdateExecutable, 'start'" not in rollback
    assert "if HostServiceExistedBeforeInstall then\n      Exec(HostExecutable, 'start'" not in rollback
    assert "HostServiceExistedBeforeInstall := ServiceExists('PartyOpsHost')" in script
    assert "UpdateServiceExistedBeforeInstall := ServiceExists('PartyOpsUpdateService')" in script
    assert "Pos('url:partyops ', Lowercase(DisplayValue))" not in script
    assert "Pos('partyopsfileopen.exe', Lowercase(CommandValue))" not in script
    assert "CompareText(CommandValue, ExpectedCommand) = 0" in script
    assert script.index("RunChecked(\n      ExpandConstant('{app}\\PartyOpsUpdaterService.exe')") < script.index(
        "    BeginInstallerCacheTransaction;"
    )
    assert script.index("    BeginInstallerCacheTransaction;") < script.index(
        "    BeginDataMarkerTransaction;"
    )


def test_update_archive_and_transfer_ids_reject_ambiguous_paths(tmp_path: Path) -> None:
    package = tmp_path / "duplicate.partyops-update"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("manifest.json", "{}")
            archive.writestr("manifest.json", "{}")
    with pytest.raises(Exception) as duplicate:
        updates_router._extract_manifest(package)
    assert getattr(duplicate.value, "code", "") == "UPDATE_DUPLICATE_MEMBER"

    with pytest.raises(client_agent.AgentCommandError) as escaped:
        client_agent._validated_transfer_id("../../outside")
    assert escaped.value.code == "TRANSFER_ID_INVALID"
    assert client_agent._validated_transfer_id("transfer-1") == "transfer-1"

    with pytest.raises(client_agent.AgentCommandError) as oversized_chunk:
        client_agent._validated_transfer_geometry(
            {
                "chunk_size": client_agent.MAX_TRANSFER_CHUNK_BYTES + 1,
                "total_chunks": 1,
                "size_bytes": 1,
            }
        )
    assert oversized_chunk.value.code == "TRANSFER_METADATA_INVALID"

    with pytest.raises(client_agent.AgentCommandError) as inconsistent:
        client_agent._validated_transfer_geometry(
            {"chunk_size": 8, "total_chunks": 1, "size_bytes": 9}
        )
    assert inconsistent.value.code == "TRANSFER_METADATA_INVALID"
