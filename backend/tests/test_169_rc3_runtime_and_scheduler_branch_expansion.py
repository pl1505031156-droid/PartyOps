"""本地模型进程与调度器在资源失败、去重和退化状态下的分支回归。"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app import local_ai, scheduler
from app.models import Notification
from app.problems import ProblemException


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


def _code(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


def test_embedding_runtime_reuses_pack_and_handles_sparse_inputs(monkeypatch) -> None:
    class Encoded:
        ids = [1, 2]
        attention_mask = [1, 1]
        type_ids = [0, 0]

    class Tokenizer:
        @classmethod
        def from_file(cls, _path):
            return cls()

        def encode_batch(self, texts):
            assert texts == ["query:内容"]
            return [Encoded()]

    class Session:
        def __init__(self, *_args, **_kwargs):
            self.created = True

        def get_inputs(self):
            # 缺少可选输入名时不能误造字段，模型仍可按其契约运行。
            return []

        def run(self, _outputs, inputs):
            assert inputs == {}
            return [np.asarray([[3.0, 4.0]], dtype=np.float32)]

    ort = types.ModuleType("onnxruntime")
    ort.SessionOptions = lambda: SimpleNamespace()
    ort.InferenceSession = Session
    tokenizers = types.ModuleType("tokenizers")
    tokenizers.Tokenizer = Tokenizer
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", tokenizers)
    monkeypatch.setattr(local_ai, "_component_file", lambda *_args: Path("model.onnx"))
    monkeypatch.setattr(
        local_ai,
        "get_settings",
        lambda: SimpleNamespace(local_ai_max_threads=2),
    )
    pack = SimpleNamespace(
        id="embedding-1",
        manifest={
            "components": {
                "embedding": {
                    "query_prefix": "query:",
                    "max_length": 64,
                    "dimension": 2,
                }
            }
        },
    )
    runtime = local_ai.EmbeddingRuntime()
    first = runtime.encode(pack, ["内容"], is_query=True)
    first_session = runtime._session
    second = runtime.encode(pack, ["内容"], is_query=True)
    assert first == second and runtime._session is first_session
    assert np.frombuffer(first[0], dtype=np.float32).tolist() == pytest.approx([0.6, 0.8])


def test_llm_binary_job_limit_handle_and_early_return(monkeypatch) -> None:
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    monkeypatch.setattr(local_ai.os, "access", lambda *_args: True)
    assert local_ai.LocalLlmRuntime._binary().endswith("llama-server")

    runtime = local_ai.LocalLlmRuntime()
    process = SimpleNamespace(poll=lambda: None)
    runtime._process = process
    runtime._pack_id = "pack-1"
    runtime._ensure_started(SimpleNamespace(id="pack-1"))
    assert runtime._process is process

    closed = []
    runtime._process = None
    runtime._windows_job_handle = 42
    monkeypatch.setattr(
        __import__("ctypes").windll.kernel32,
        "CloseHandle",
        lambda handle: closed.append(handle),
    )
    runtime.stop()
    assert closed == [42] and runtime._windows_job_handle is None


def test_windows_job_object_failure_paths(monkeypatch) -> None:
    runtime = local_ai.LocalLlmRuntime()
    ctypes = __import__("ctypes")

    missing = SimpleNamespace(CreateJobObjectW=lambda *_args: 0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: missing)
    with pytest.raises(OSError, match="无法创建"):
        runtime._apply_windows_job_limit(SimpleNamespace(_handle=1))

    closed = []
    rejected = SimpleNamespace(
        CreateJobObjectW=lambda *_args: 99,
        SetInformationJobObject=lambda *_args: 0,
        AssignProcessToJobObject=lambda *_args: 1,
        CloseHandle=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: rejected)
    with pytest.raises(OSError, match="无法应用"):
        runtime._apply_windows_job_limit(SimpleNamespace(_handle=1))
    assert closed == [99]


def test_llm_start_posix_spawn_and_failure_branches(monkeypatch) -> None:
    settings = SimpleNamespace(local_ai_port=18888, local_ai_max_threads=2)
    pack = SimpleNamespace(id="llm-1")
    runtime = local_ai.LocalLlmRuntime()
    monkeypatch.setattr(runtime, "_binary", lambda: "/opt/partyops/bin/llama-server")
    monkeypatch.setattr(local_ai, "_component_file", lambda *_args: Path("model.gguf"))
    monkeypatch.setattr(local_ai, "get_settings", lambda: settings)
    monkeypatch.setattr(local_ai, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        local_ai.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    _code("LOCAL_LLM_START_FAILED", lambda: runtime._ensure_started(pack))
    assert runtime._start_failures[pack.id][0] == 1

    class DeadProcess:
        def poll(self):
            return 7

        def terminate(self):
            return None

    runtime._start_failures.clear()
    monkeypatch.setattr(local_ai.subprocess, "Popen", lambda *_args, **_kwargs: DeadProcess())
    monkeypatch.setattr(runtime, "_apply_windows_job_limit", lambda _process: None)
    monkeypatch.setattr(local_ai.time, "sleep", lambda _seconds: None)
    _code("LOCAL_LLM_START_FAILED", lambda: runtime._ensure_started(pack))
    assert runtime._process is None


def test_llm_health_never_ready_stops_after_bounded_polling(monkeypatch) -> None:
    settings = SimpleNamespace(local_ai_port=18888, local_ai_max_threads=2)
    pack = SimpleNamespace(id="llm-timeout")
    runtime = local_ai.LocalLlmRuntime()

    class Process:
        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, **_kwargs):
            return None

    process = Process()
    monkeypatch.setattr(runtime, "_binary", lambda: "llama-server.exe")
    monkeypatch.setattr(local_ai, "_component_file", lambda *_args: "model.gguf")
    monkeypatch.setattr(local_ai, "get_settings", lambda: settings)
    monkeypatch.setattr(local_ai.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(runtime, "_apply_windows_job_limit", lambda _process: None)
    monkeypatch.setattr(
        local_ai.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503),
    )
    _code("LOCAL_LLM_START_FAILED", lambda: runtime._ensure_started(pack))
    assert process.terminated is True and runtime._process is None


def test_automation_empty_suggestions_and_scheduler_backup_skip(monkeypatch) -> None:
    admin = SimpleNamespace(id="admin-1")
    rule = SimpleNamespace(
        id="file-rule",
        name="空建议规则",
        trigger="workspace_file_indexed",
        owner_id="owner-1",
        conditions={},
        actions={},
    )
    file = SimpleNamespace(
        id="file-1",
        name="报告.pdf",
        relative_path="年度/报告.pdf",
        extension="pdf",
        version=1,
    )

    class RuleDb:
        def __init__(self):
            self.batches = iter([[rule], [file]])
            self.added = []

        def scalars(self, _statement):
            return _Rows(next(self.batches))

        def scalar(self, _statement):
            return None

        def add(self, value):
            self.added.append(value)

    db = RuleDb()
    scheduler.run_automation_rules(db, admin)
    notification = next(item for item in db.added if isinstance(item, Notification))
    assert notification.body == "文件“报告.pdf”符合规则“空建议规则”。"

    # 当天已有自动备份时只记录调度日，不重复生成第二份备份。
    now = datetime(2026, 8, 25, 2, 30, tzinfo=UTC)
    latest = SimpleNamespace(created_at=now)
    revoked = SimpleNamespace(status=SimpleNamespace(value="revoked"), last_seen_at=now)

    class Session:
        def __init__(self, scalar_values, scalar_rows):
            self.scalar_values = iter(scalar_values)
            self.scalar_rows = iter(scalar_rows)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _statement):
            return next(self.scalar_values, None)

        def scalars(self, _statement):
            return _Rows(next(self.scalar_rows, []))

        def begin_nested(self):
            return SimpleNamespace(
                __enter__=lambda self: self,
                __exit__=lambda self, *_args: False,
            )

        def commit(self):
            return None

    sessions = iter(
        [
            Session([latest], []),
            Session([None], [[revoked]]),
        ]
    )
    monkeypatch.setattr(scheduler.db_runtime, "session_factory", lambda: next(sessions))
    backup_calls = []
    monkeypatch.setattr(scheduler, "create_backup", lambda *_args, **_kwargs: backup_calls.append(1))
    monkeypatch.setattr(scheduler, "refresh_notifications", lambda *_args: None)
    monkeypatch.setattr(scheduler, "cleanup_transfer_storage", lambda *_args: 0)
    monkeypatch.setattr(scheduler, "cleanup_runtime_retention", lambda *_args, **_kwargs: {})
    settings = SimpleNamespace(backup_hour=2, backup_minute=0)
    result = scheduler._run_scheduler_cycle(settings, now, None)
    assert result == "2026-08-25" and backup_calls == []
