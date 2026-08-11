"""本地 AI 内存探测、退避、进程回收和降级分支回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import local_ai
from app.problems import ProblemException


def assert_problem(code: str, call) -> None:
    with pytest.raises(ProblemException) as error:
        call()
    assert error.value.code == code


def test_available_memory_linux_proc_sysconf_and_failure(monkeypatch) -> None:
    class Proc:
        text = "MemAvailable: 2048 kB"

        def read_text(self, **_kwargs):
            if isinstance(self.text, Exception):
                raise self.text
            return self.text

    monkeypatch.setattr(local_ai, "Path", lambda _value: Proc())
    monkeypatch.setattr(local_ai, "os", SimpleNamespace(name="posix", sysconf=lambda key: {"SC_AVPHYS_PAGES": 8, "SC_PAGE_SIZE": 4096}[key]))
    assert local_ai._available_memory_mb() == 2

    Proc.text = "MemTotal: 4096 kB"
    assert local_ai._available_memory_mb() == 0

    Proc.text = OSError("no proc")
    monkeypatch.setattr(local_ai, "os", SimpleNamespace(name="posix", sysconf=lambda _key: (_ for _ in ()).throw(OSError())))
    assert local_ai._available_memory_mb() is None


def test_llm_start_backoff_records_retries(monkeypatch) -> None:
    runtime = local_ai.LocalLlmRuntime()
    monkeypatch.setattr(local_ai.time, "monotonic", lambda: 100.0)
    runtime._raise_if_start_backoff("new")
    runtime._start_failures["expired"] = (1, 99.0, "旧失败")
    runtime._raise_if_start_backoff("expired")
    runtime._start_failures["active"] = (1, 101.2, "启动失败")
    assert_problem("LOCAL_LLM_START_BACKOFF", lambda: runtime._raise_if_start_backoff("active"))

    runtime._record_start_failure("retry", "第一次")
    assert runtime._start_failures["retry"][0] == 1
    runtime._record_start_failure("retry", "第二次")
    assert runtime._start_failures["retry"][0] == 2


def test_llm_binary_fallback_process_stop_idle_and_status(monkeypatch) -> None:
    monkeypatch.setattr(local_ai.shutil, "which", lambda name: f"C:/runtime/{name}.exe")
    assert local_ai.LocalLlmRuntime._binary().endswith("llama-server.exe")

    class Process:
        pid = 42

        def __init__(self, running=True, timeout=False) -> None:
            self.running = running
            self.timeout = timeout
            self.terminated = False
            self.killed = False

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            self.terminated = True

        def wait(self, **_kwargs):
            if self.timeout:
                raise local_ai.subprocess.TimeoutExpired("llama", 8)

        def kill(self):
            self.killed = True

    runtime = local_ai.LocalLlmRuntime()
    assert runtime.status() == (False, None)
    alive = Process()
    runtime._process = alive
    assert runtime.status() == (True, 42)
    runtime.stop()
    assert alive.terminated and runtime.status() == (False, None)

    timeout = Process(timeout=True)
    runtime._process = timeout
    runtime.stop()
    assert timeout.killed

    dead = Process(running=False)
    runtime._process = dead
    assert runtime.status() == (False, None)
    runtime.stop()
    assert not dead.terminated

    runtime._process = Process()
    runtime._last_used = 1
    calls = []
    monkeypatch.setattr(local_ai.time, "monotonic", lambda: 400)
    monkeypatch.setattr(runtime, "stop", lambda: calls.append("stop"))
    runtime.unload_if_idle(300)
    assert calls == ["stop"]
    calls.clear()
    runtime._last_used = 200
    runtime.unload_if_idle(300)
    assert calls == []


def test_non_windows_job_limit_is_noop(monkeypatch) -> None:
    runtime = local_ai.LocalLlmRuntime()
    monkeypatch.setattr(local_ai, "os", SimpleNamespace(name="posix"))
    runtime._apply_windows_job_limit(SimpleNamespace())


def test_runtime_status_and_complete_local_degradation(monkeypatch) -> None:
    monkeypatch.setattr(local_ai, "get_settings", lambda: SimpleNamespace(local_ai_max_threads=9, local_ai_memory_limit_mb=8192))
    monkeypatch.setattr(local_ai.llm_runtime, "status", lambda: (True, 123))
    monkeypatch.setattr(local_ai.embedding_runtime, "loaded_for", lambda pack_id: pack_id == "embedding-1")
    monkeypatch.setattr(
        local_ai,
        "local_ai_readiness",
        lambda _db, capability=None: {
            "ready": capability != "llm",
            "message": "未启用",
            "embedding_pack_id": "embedding-1",
            "embedding_available": True,
            "llm_available": False,
        },
    )
    status = local_ai.local_runtime_status(SimpleNamespace())
    assert status["llm_running"] is True and status["embedding_loaded"] is True
    assert status["max_threads"] == 4 and status["memory_limit_mb"] == 8192
    assert_problem("LOCAL_AI_UNAVAILABLE", lambda: local_ai.complete_locally(SimpleNamespace(), "生成", []))

    monkeypatch.setattr(local_ai, "local_ai_readiness", lambda *_a, **_k: {"ready": True, "message": ""})
    monkeypatch.setattr(local_ai, "active_model_pack", lambda *_a: None)
    assert_problem("LOCAL_AI_UNAVAILABLE", lambda: local_ai.complete_locally(SimpleNamespace(), "生成", []))

    pack = SimpleNamespace(id="llm-1")
    monkeypatch.setattr(local_ai, "active_model_pack", lambda *_a: pack)
    monkeypatch.setattr(local_ai.llm_runtime, "complete", lambda actual, instruction, excerpts: f"{actual.id}:{instruction}:{len(excerpts)}")
    assert local_ai.complete_locally(SimpleNamespace(), "生成", ["资料"]) == "llm-1:生成:1"
