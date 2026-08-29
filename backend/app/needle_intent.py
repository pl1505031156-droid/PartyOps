"""Needle 2 原生离线意图预览与对抗式结果门禁。

本模块直接使用 Needle 官方原生 C ABI，只调用 ``complete``，绝不注册或
执行真实业务函数。模型输出必须通过白名单、JSON 结构、置信度、否定词、
提示注入和输入证据校验；任何失败都回退到规则预览。
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .enums import ModelPackStatus
from .intent_preview import preview_intent
from .model_packs import active_model_pack, model_pack_root, verify_installed_pack
from .models import AIModelPack
from .time_utils import beijing_now

INTENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "task_create",
        "description": "把用户明确要求新建的事项、任务、待办或提醒整理为预览，不执行创建",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "输入中明确出现的事项内容"},
                "due_date": {"type": "string", "description": "仅在输入明确给出日期时填写 YYYY-MM-DD"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "task_delete",
        "description": "识别用户明确提出的删除或移除请求，只生成高风险预览",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "message_send",
        "description": "识别用户明确提出的发送、转发或通知请求，只生成预览",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "recipient": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "data_export",
        "description": "识别导出台账、名单、档案、记录或文件的请求，只生成预览",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "role_switch",
        "description": "识别重新配置个人、主机或协同运行身份的请求，只生成预览",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["personal", "host", "client"],
                }
            },
            "required": ["mode"],
        },
    },
    {
        "name": "navigation_find",
        "description": "定位 PartyOps 中用户询问在哪里、怎么打开或如何进入的功能",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
    },
    {
        "name": "search_query",
        "description": "识别查询、搜索、查看或找一下系统现有资料的请求",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]

INTENT_MAP = {
    "task_create": ("task.create", "write"),
    "task_delete": ("task.delete", "write"),
    "message_send": ("message.send", "write"),
    "data_export": ("data.export", "write"),
    "role_switch": ("role.switch", "write"),
    "navigation_find": ("navigation.find", "read"),
    "search_query": ("search.query", "read"),
}
ARGUMENTS = {
    "task_create": {"title", "due_date"},
    "task_delete": {"target"},
    "message_send": {"content", "recipient"},
    "data_export": {"target"},
    "role_switch": {"mode"},
    "navigation_find": {"target"},
    "search_query": {"query"},
}


def _component_file(pack: AIModelPack, key: str, *, required: bool = True) -> Path | None:
    component = pack.manifest.get("components", {}).get("intent_router", {})
    value = component.get(key) if isinstance(component, dict) else None
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Needle 组件缺少 {key}")
    root = model_pack_root(pack)
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file():
        raise RuntimeError(f"Needle 组件 {key} 不在受管模型目录")
    return path


class NeedleIntentRuntime:
    """按激活包延迟加载原生库，所有调用在单进程锁内串行完成。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pack_id = ""
        self._library: Any = None
        self._weights_blob: bytes | None = None
        self._dll_directory: Any = None

    def unload(self) -> None:
        with self._lock:
            self._pack_id = ""
            self._library = None
            self._weights_blob = None
            if self._dll_directory is not None:
                self._dll_directory.close()
                self._dll_directory = None

    @staticmethod
    def _validate_suffix(path: Path) -> None:
        expected = ".dll" if os.name == "nt" else ".dylib" if os.sys.platform == "darwin" else ".so"
        if path.suffix.lower() != expected:
            raise RuntimeError(f"Needle 运行时必须是当前平台的 {expected} 文件")

    def _load(self, pack: AIModelPack) -> Any:
        if self._pack_id == pack.id and self._library is not None:
            return self._library
        runtime_path = _component_file(pack, "runtime_file")
        assert runtime_path is not None
        self._validate_suffix(runtime_path)
        if os.name == "nt" and hasattr(os, "add_dll_directory"):
            self._dll_directory = os.add_dll_directory(str(runtime_path.parent))
        library = ctypes.CDLL(str(runtime_path))
        library.needle_init.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        library.needle_init.restype = ctypes.c_int
        library.needle_complete.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        library.needle_complete.restype = ctypes.c_int
        library.needle_reset.argtypes = []
        library.needle_reset.restype = None
        library.needle_load.argtypes = [ctypes.c_char_p, ctypes.c_uint64]
        library.needle_load.restype = ctypes.c_int
        weights_path = _component_file(pack, "model_file", required=False)
        self._weights_blob = None
        if weights_path is not None:
            blob = weights_path.read_bytes()
            if not blob or len(blob) > 512 * 1024 * 1024:
                raise RuntimeError("Needle 可选权重文件大小无效")
            if library.needle_load(blob, len(blob)) != 0:
                raise RuntimeError("Needle 可选权重与当前原生运行时不兼容")
            self._weights_blob = blob
        self._library = library
        self._pack_id = pack.id
        return library

    def probe(self, pack: AIModelPack) -> None:
        """激活前验证签名包、平台库格式和必需 ABI，不执行任何业务工具。"""

        if pack.status == ModelPackStatus.CORRUPT or not verify_installed_pack(pack):
            raise RuntimeError("Needle 模型包完整性校验失败")
        with self._lock:
            self._load(pack)

    @staticmethod
    def available(pack: AIModelPack) -> bool:
        """只检查当前平台文件边界，不在状态轮询中加载模型。"""

        try:
            runtime_path = _component_file(pack, "runtime_file")
            assert runtime_path is not None
            NeedleIntentRuntime._validate_suffix(runtime_path)
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    def complete(self, pack: AIModelPack, text: str, *, today: date) -> dict[str, Any]:
        with self._lock:
            library = self._load(pack)
            system = f"date: {today.isoformat()}; locale: zh-CN; assistant: PartyOps".encode()
            tools = json.dumps(
                INTENT_TOOLS,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            if library.needle_init(system, tools, None) < 0:
                raise RuntimeError("needle_init failed")
            buffer = ctypes.create_string_buffer(64 * 1024)
            result = library.needle_complete(text.encode("utf-8"), 192, buffer, len(buffer))
            if result < 0:
                raise RuntimeError(f"needle_complete failed ({result})")
            try:
                payload = json.loads(buffer.value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Needle 返回了无效 JSON") from exc
            if not isinstance(payload, dict):
                raise TypeError("Needle 返回结构不是对象")
            return payload


needle_intent_runtime = NeedleIntentRuntime()


def _fallback(base: dict[str, Any], flag: str) -> dict[str, Any]:
    payload = {**base, "engine": "rules"}
    payload["flags"] = sorted({*base.get("flags", []), flag})
    return payload


def _arguments_evidenced(name: str, arguments: dict[str, Any], text: str, today: date) -> bool:
    if set(arguments) - ARGUMENTS[name]:
        return False
    for key, value in arguments.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 240:
            return False
        if key == "due_date":
            expected = preview_intent(text, today=today)["preview"].get("due_date")
            if value != expected:
                return False
        elif name == "role_switch" and key == "mode":
            evidence = {
                "personal": "个人",
                "host": "主机",
                "client": "协同",
            }.get(value, "")
            if not evidence or evidence not in text:
                return False
        elif value not in text:
            return False
    return True


def preview_intent_with_needle(
    db: Session,
    text: str,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """优先使用已激活的 Needle；任何不确定性都安全回退到规则结果。"""

    current = today or beijing_now().date()
    base = preview_intent(text, today=current)
    if {"PROMPT_INJECTION", "NEGATED"} & set(base["flags"]):
        return base
    pack = active_model_pack(db, "intent_router")
    if not pack:
        return base
    if pack.status == ModelPackStatus.CORRUPT or not verify_installed_pack(pack):
        return _fallback(base, "NEEDLE_PACK_INVALID")
    try:
        response = needle_intent_runtime.complete(pack, text, today=current)
    except (OSError, RuntimeError, ValueError):
        return _fallback(base, "NEEDLE_RUNTIME_ERROR")
    calls = response.get("function_calls")
    confidence = response.get("confidence")
    component = pack.manifest.get("components", {}).get("intent_router", {})
    try:
        threshold = float(component.get("confidence_threshold", 0.82))
    except (TypeError, ValueError):
        threshold = 0.82
    threshold = min(0.99, max(0.5, threshold))
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return _fallback(base, "NEEDLE_CONFIDENCE_INVALID")
    if confidence < threshold:
        return _fallback(base, "NEEDLE_LOW_CONFIDENCE")
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return _fallback(base, "NEEDLE_NO_UNIQUE_INTENT")
    name = calls[0].get("name")
    arguments = calls[0].get("arguments")
    if name not in INTENT_MAP or not isinstance(arguments, dict):
        return _fallback(base, "NEEDLE_SCHEMA_REJECTED")
    if not _arguments_evidenced(str(name), arguments, text, current):
        return _fallback(base, "NEEDLE_ARGUMENT_NOT_EVIDENCED")
    intent, operation = INTENT_MAP[str(name)]
    preview_title = next(
        (
            str(arguments[key])
            for key in ("title", "target", "query", "content", "mode")
            if key in arguments
        ),
        text.strip()[:120],
    )
    flags = list(base["flags"])
    clarification = base["clarification"] if "SENSITIVE_CONTENT" in flags else None
    return {
        "engine": "needle",
        "intent": intent,
        "operation": operation,
        "confidence": float(confidence),
        "requires_confirmation": operation == "write",
        "can_execute": False,
        "clarification": clarification,
        "flags": flags,
        "preview": {
            "title": preview_title[:120],
            "due_date": arguments.get("due_date"),
            "source_text": " ".join(text.strip().split()),
        },
        "notice": "Needle 仅生成结构化预览；任何写操作都必须由用户确认，并由 PartyOps 服务端重新校验权限和参数。",
    }


__all__ = [
    "NeedleIntentRuntime",
    "needle_intent_runtime",
    "preview_intent_with_needle",
]
