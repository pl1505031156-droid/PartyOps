from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import startup_selftest


def test_critical_crypto_roundtrip_uses_real_runtime() -> None:
    startup_selftest._critical_crypto_roundtrip()


def test_crypto_selftest_rejects_each_broken_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.asymmetric import rsa

    class _BrokenPublic:
        def encrypt(self, *_args):
            return b"cipher"

    class _BrokenPrivate:
        def public_key(self):
            return _BrokenPublic()

        def decrypt(self, *_args):
            return b"wrong"

    monkeypatch.setattr(rsa, "generate_private_key", lambda **_kwargs: _BrokenPrivate())
    with pytest.raises(RuntimeError, match="RSA OAEP"):
        startup_selftest._critical_crypto_roundtrip()
    monkeypatch.undo()

    monkeypatch.setattr(rsa, "rsa_recover_private_exponent", lambda *_args: 0)
    with pytest.raises(RuntimeError, match="私钥恢复"):
        startup_selftest._critical_crypto_roundtrip()
    monkeypatch.undo()

    monkeypatch.setattr(Fernet, "decrypt", lambda *_args, **_kwargs: b"wrong")
    with pytest.raises(RuntimeError, match="Fernet"):
        startup_selftest._critical_crypto_roundtrip()


def test_validate_probe_requires_version_mode_and_frontend() -> None:
    startup_selftest._validate_probe(
        {
            "status": "ok",
            "app_version": "1.4.5-rc.2",
            "mode": "personal",
            "sqlite": {"safe_version": True, "fts5": True},
        },
        b'<!doctype html><div id="app"></div>',
    )
    with pytest.raises(RuntimeError, match="运行版本不一致"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "old",
                "mode": "personal",
                "sqlite": {"safe_version": True, "fts5": True},
            },
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="自检模式不一致"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "1.4.5-rc.2",
                "mode": "host",
                "sqlite": {"safe_version": True, "fts5": True},
            },
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="SQLite"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "1.4.5-rc.2",
                "mode": "personal",
                "sqlite": {"safe_version": True, "fts5": False},
            },
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="首页静态入口不完整"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "1.4.5-rc.2",
                "mode": "personal",
                "sqlite": {"safe_version": True, "fts5": True},
            },
            b"not-html",
        )
    with pytest.raises(RuntimeError, match="未返回 ok"):
        startup_selftest._validate_probe(
            {"status": "failed", "app_version": "1.4.5-rc.2", "mode": "personal", "sqlite": {}},
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="SQLite"):
        startup_selftest._validate_probe(
            {"status": "ok", "app_version": "1.4.5-rc.2", "mode": "personal", "sqlite": "invalid"},
            b'<!doctype html><div id="app"></div>',
        )


class _Response:
    def __init__(self, status: int, payload: bytes) -> None:
        self.status = status
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args) -> bytes:
        return self.payload


def test_http_probe_and_log_tail_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log = tmp_path / "probe.log"
    log.write_text("前缀" + "x" * 5000, encoding="utf-8")
    assert len(startup_selftest._tail(log, 20)) <= 20
    assert startup_selftest._tail(tmp_path / "missing.log") == ""
    monkeypatch.setattr(startup_selftest.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(200, b'{"status":"ok"}'))
    assert startup_selftest._read_json("http://127.0.0.1/health")["status"] == "ok"
    assert startup_selftest._read_frontend("http://127.0.0.1/") == b'{"status":"ok"}'
    monkeypatch.setattr(startup_selftest.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response(503, b"failed"))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        startup_selftest._read_json("http://127.0.0.1/health")
    with pytest.raises(RuntimeError, match="HTTP 503"):
        startup_selftest._read_frontend("http://127.0.0.1/")


class _Process:
    def __init__(self, polls: list[int | None], *, wait_timeout: bool = False) -> None:
        self.polls = iter(polls)
        self.last_poll: int | None = None
        self.returncode: int | None = None
        self.wait_timeout = wait_timeout
        self.terminated = False
        self.killed = False

    def poll(self):
        try:
            self.last_poll = next(self.polls)
        except StopIteration:
            pass
        self.returncode = self.last_poll
        return self.last_poll

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> None:
        if self.wait_timeout and not self.killed:
            raise subprocess.TimeoutExpired("PartyOps", timeout)
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True


def test_frozen_probe_success_exit_and_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "_internal" / "frontend").mkdir(parents=True)
    success = _Process([None, None])
    monkeypatch.setattr(startup_selftest.subprocess, "Popen", lambda *_args, **_kwargs: success)
    monkeypatch.setattr(startup_selftest, "_reserve_loopback_port", lambda: 18888)
    monkeypatch.setattr(startup_selftest, "_read_json", lambda _url: {"status": "ok", "app_version": "1.4.5-rc.2", "mode": "personal", "sqlite": {"safe_version": True, "fts5": True}})
    monkeypatch.setattr(startup_selftest, "_read_frontend", lambda _url: b'<!doctype html><div id="app"></div>')
    assert startup_selftest._probe_frozen_server(runtime, 10)["status"] == "ok"
    assert success.terminated is True

    no_frontend_runtime = tmp_path / "runtime-without-frontend"
    no_frontend_runtime.mkdir()
    no_frontend = _Process([None, None])
    monkeypatch.setattr(startup_selftest.subprocess, "Popen", lambda *_args, **_kwargs: no_frontend)
    assert startup_selftest._probe_frozen_server(no_frontend_runtime, 10)["status"] == "ok"

    exited = _Process([7])
    monkeypatch.setattr(startup_selftest.subprocess, "Popen", lambda *_args, **_kwargs: exited)
    with pytest.raises(RuntimeError, match="提前退出"):
        startup_selftest._probe_frozen_server(runtime, 10)

    timed_out = _Process([None, None, None], wait_timeout=True)
    monkeypatch.setattr(startup_selftest.subprocess, "Popen", lambda *_args, **_kwargs: timed_out)
    ticks = iter([0.0, 0.0, 11.0])
    monkeypatch.setattr(startup_selftest.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(startup_selftest, "_read_json", lambda _url: (_ for _ in ()).throw(OSError("未监听")))
    monkeypatch.setattr(startup_selftest.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="未就绪"):
        startup_selftest._probe_frozen_server(runtime, 10)
    assert timed_out.terminated is True and timed_out.killed is True


def test_main_reports_success_and_bounded_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        startup_selftest,
        "run_selftest",
        lambda _runtime: {
            "passed": True,
            "version": "1.4.5-rc.2",
            "mode": "personal",
        },
    )
    assert startup_selftest.main(Path("runtime")) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    def fail(_runtime: Path) -> dict[str, object]:
        raise RuntimeError("x" * 7000)

    monkeypatch.setattr(startup_selftest, "run_selftest", fail)
    assert startup_selftest.main(Path("runtime")) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "PACKAGE_RUNTIME_STARTUP_SELFTEST_FAILED"
    assert len(payload["error"]) == 6000
