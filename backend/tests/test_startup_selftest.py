from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import startup_selftest


def test_critical_crypto_roundtrip_uses_real_runtime() -> None:
    startup_selftest._critical_crypto_roundtrip()


def test_validate_probe_requires_version_mode_and_frontend() -> None:
    startup_selftest._validate_probe(
        {
            "status": "ok",
            "app_version": "1.4.3-rc.9",
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
                "app_version": "1.4.3-rc.9",
                "mode": "host",
                "sqlite": {"safe_version": True, "fts5": True},
            },
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="SQLite"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "1.4.3-rc.9",
                "mode": "personal",
                "sqlite": {"safe_version": True, "fts5": False},
            },
            b'<!doctype html><div id="app"></div>',
        )
    with pytest.raises(RuntimeError, match="首页静态入口不完整"):
        startup_selftest._validate_probe(
            {
                "status": "ok",
                "app_version": "1.4.3-rc.9",
                "mode": "personal",
                "sqlite": {"safe_version": True, "fts5": True},
            },
            b"not-html",
        )


def test_main_reports_success_and_bounded_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        startup_selftest,
        "run_selftest",
        lambda _runtime: {
            "passed": True,
            "version": "1.4.3-rc.9",
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
