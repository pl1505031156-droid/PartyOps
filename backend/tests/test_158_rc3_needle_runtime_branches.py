"""Needle 原生 ABI、文件边界和输出证据校验分支。"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from app import needle_intent as needle
from app.enums import ModelPackStatus


class _Function:
    def __init__(self, result=0, effect=None):
        self.result = result
        self.effect = effect
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        if self.effect:
            self.effect(*args)
        return self.result


class _Library:
    def __init__(self, *, init=0, complete=1, payload=None, load=0):
        self.needle_init = _Function(init)

        def write(_text, _limit, buffer, _length):
            if payload is not None:
                buffer.value = payload

        self.needle_complete = _Function(complete, write)
        self.needle_reset = _Function(None)
        self.needle_load = _Function(load)


def _pack(runtime="models/intent/libneedle.dll", *, model_file=None, status=ModelPackStatus.ACTIVE):
    component = {"runtime_file": runtime}
    if model_file is not None:
        component["model_file"] = model_file
    return SimpleNamespace(id="pack-1", status=status, manifest={"components": {"intent_router": component}})


def test_component_file_boundaries(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(needle, "model_pack_root", lambda _pack: tmp_path.resolve())
    assert needle._component_file(_pack(), "model_file", required=False) is None
    with pytest.raises(RuntimeError, match="缺少 runtime_file"):
        needle._component_file(SimpleNamespace(manifest={"components": {"intent_router": "bad"}}), "runtime_file")
    with pytest.raises(RuntimeError, match="缺少 runtime_file"):
        needle._component_file(SimpleNamespace(manifest={"components": {"intent_router": {"runtime_file": 3}}}), "runtime_file")
    with pytest.raises(RuntimeError, match="不在受管模型目录"):
        needle._component_file(_pack("../outside.dll"), "runtime_file")
    with pytest.raises(RuntimeError, match="不在受管模型目录"):
        needle._component_file(_pack("models/missing.dll"), "runtime_file")
    runtime = tmp_path / "models" / "intent" / "libneedle.dll"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"MZ")
    assert needle._component_file(_pack(), "runtime_file") == runtime.resolve()


def test_runtime_suffix_unload_available_and_probe(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = needle.NeedleIntentRuntime()
    closed = []
    runtime._dll_directory = SimpleNamespace(close=lambda: closed.append(True))
    runtime._pack_id = "pack"
    runtime._library = object()
    runtime._weights_blob = b"x"
    runtime.unload()
    assert closed == [True] and runtime._library is None and runtime._weights_blob is None
    runtime.unload()

    suffix = ".dll" if needle.os.name == "nt" else ".dylib" if needle.os.sys.platform == "darwin" else ".so"
    needle.NeedleIntentRuntime._validate_suffix(tmp_path / f"libneedle{suffix}")
    with pytest.raises(RuntimeError, match="当前平台"):
        needle.NeedleIntentRuntime._validate_suffix(tmp_path / "libneedle.wrong")

    pack = _pack()
    monkeypatch.setattr(needle, "_component_file", lambda *_args, **_kwargs: tmp_path / f"libneedle{suffix}")
    assert needle.NeedleIntentRuntime.available(pack) is True
    monkeypatch.setattr(needle, "_component_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")))
    assert needle.NeedleIntentRuntime.available(pack) is False
    monkeypatch.setattr(needle, "verify_installed_pack", lambda _pack: False)
    with pytest.raises(RuntimeError, match="完整性"):
        runtime.probe(pack)
    corrupt = _pack(status=ModelPackStatus.CORRUPT)
    with pytest.raises(RuntimeError, match="完整性"):
        runtime.probe(corrupt)


def test_runtime_load_weights_cache_and_failures(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_path = tmp_path / "libneedle.dll"
    weights_path = tmp_path / "weights.bin"
    runtime_path.write_bytes(b"MZ")
    weights_path.write_bytes(b"weights")
    pack = _pack("libneedle.dll", model_file="weights.bin")
    monkeypatch.setattr(needle, "_component_file", lambda _pack, key, **_kwargs: runtime_path if key == "runtime_file" else weights_path)
    monkeypatch.setattr(needle.os, "name", "nt")
    handles = []
    monkeypatch.setattr(needle.os, "add_dll_directory", lambda _path: SimpleNamespace(close=lambda: handles.append("closed")), raising=False)
    library = _Library(payload=b'{"function_calls":[],"confidence":0.8}')
    monkeypatch.setattr(needle.ctypes, "CDLL", lambda _path: library)
    runtime = needle.NeedleIntentRuntime()
    assert runtime._load(pack) is library
    assert runtime._weights_blob == b"weights"
    assert runtime._load(pack) is library
    runtime.unload()
    assert handles == ["closed"]

    weights_path.write_bytes(b"")
    runtime = needle.NeedleIntentRuntime()
    with pytest.raises(RuntimeError, match="大小无效"):
        runtime._load(pack)
    weights_path.write_bytes(b"weights")
    monkeypatch.setattr(needle.ctypes, "CDLL", lambda _path: _Library(load=1))
    with pytest.raises(RuntimeError, match="不兼容"):
        needle.NeedleIntentRuntime()._load(pack)


@pytest.mark.parametrize(
    ("library", "message"),
    [
        (_Library(init=-1), "needle_init failed"),
        (_Library(complete=-2), "needle_complete failed"),
        (_Library(payload=b"not-json"), "无效 JSON"),
        (_Library(payload=json.dumps([1, 2]).encode()), "不是对象"),
    ],
)
def test_complete_rejects_native_failures(library: _Library, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = needle.NeedleIntentRuntime()
    monkeypatch.setattr(runtime, "_load", lambda _pack: library)
    with pytest.raises((RuntimeError, TypeError), match=message):
        runtime.complete(_pack(), "搜索会议", today=date(2026, 8, 25))
    valid = _Library(payload=b'{"function_calls":[],"confidence":0.9}')
    monkeypatch.setattr(runtime, "_load", lambda _pack: valid)
    assert runtime.complete(_pack(), "搜索会议", today=date(2026, 8, 25))["confidence"] == 0.9


def test_argument_evidence_allows_only_input_grounded_values() -> None:
    today = date(2026, 8, 25)
    assert needle._arguments_evidenced("search_query", {"unknown": "会议"}, "搜索会议", today) is False
    assert needle._arguments_evidenced("search_query", {"query": 3}, "搜索会议", today) is False
    assert needle._arguments_evidenced("search_query", {"query": "x" * 241}, "搜索会议", today) is False
    assert needle._arguments_evidenced("search_query", {"query": "会议"}, "搜索会议", today) is True
    assert needle._arguments_evidenced("search_query", {"query": "档案"}, "搜索会议", today) is False
    assert needle._arguments_evidenced("task_create", {"title": "提交材料", "due_date": "2026-08-26"}, "明天创建提交材料事项", today) is True
    assert needle._arguments_evidenced("task_create", {"title": "提交材料", "due_date": "2026-08-27"}, "明天创建提交材料事项", today) is False
    assert needle._arguments_evidenced("role_switch", {"mode": "host"}, "切换到主机模式", today) is True
    assert needle._arguments_evidenced("role_switch", {"mode": "client"}, "切换到主机模式", today) is False
    assert needle._arguments_evidenced("role_switch", {"mode": "unknown"}, "切换到 unknown 模式", today) is False
