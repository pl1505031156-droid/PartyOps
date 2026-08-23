"""受控意图预览。

本模块只把中文指令整理成候选结构，不调用任何写接口。后续即使启用
Needle，模型结果也必须经过同一白名单、否定词、歧义和确认门禁。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any


_WRITE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("task.create", re.compile(r"(?:新建|创建|添加|记下).{0,8}(?:事项|任务|待办|提醒)")),
    ("task.delete", re.compile(r"(?:删除|移除|清空).{0,8}(?:事项|任务|待办|提醒|文件|材料)")),
    ("message.send", re.compile(r"(?:发送|转发|通知).{0,8}(?:消息|通知|文件|材料|给)")),
    ("data.export", re.compile(r"(?:导出|下载).{0,8}(?:台账|数据|名单|档案|记录|文件)")),
    ("role.switch", re.compile(r"(?:切换|改成|重新配置).{0,8}(?:个人模式|主机模式|协同机|身份)")),
]

_READ_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("navigation.find", re.compile(r"(?:怎么|如何|哪里|打开|进入|查找|找到|定位)")),
    ("search.query", re.compile(r"(?:搜索|查询|找一下|有哪些|查看)")),
]

_NEGATION = re.compile(r"(?:不要|不用|别|禁止|取消|不需要|暂不)")
_INJECTION = re.compile(
    r"(?:忽略.{0,12}(?:规则|指令|权限)|绕过.{0,8}(?:确认|权限)|直接执行|无需确认|管理员模式|system prompt)",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(r"(?:发展党员|身份证|手机号|中心组发言|涉密|密码|私钥|证书)")


def _relative_date(text: str, today: date) -> str | None:
    if "今天" in text:
        return today.isoformat()
    if "明天" in text:
        return (today + timedelta(days=1)).isoformat()
    if "后天" in text:
        return (today + timedelta(days=2)).isoformat()
    if "周五" in text or "星期五" in text:
        days = (4 - today.weekday()) % 7
        return (today + timedelta(days=days or 7)).isoformat()
    match = re.search(r"(\d{1,2})月(\d{1,2})[日号]?", text)
    if match:
        try:
            target = date(today.year, int(match.group(1)), int(match.group(2)))
            if target < today:
                target = date(today.year + 1, target.month, target.day)
            return target.isoformat()
        except ValueError:
            return None
    return None


def preview_intent(text: str, *, today: date | None = None) -> dict[str, Any]:
    """返回只读结构化预览；该函数永远不返回可直接执行的写动作。"""

    normalized = " ".join(text.strip().split())
    current = today or date.today()
    flags: list[str] = []
    clarification: str | None = None
    intent = "unknown"
    operation = "none"

    if _INJECTION.search(normalized):
        flags.append("PROMPT_INJECTION")
        clarification = "检测到试图绕过权限或确认的内容，请改为描述具体业务目标。"
    elif _NEGATION.search(normalized):
        flags.append("NEGATED")
        clarification = "检测到否定或取消表达，为避免误操作不会生成执行建议。"
    else:
        for name, pattern in _WRITE_PATTERNS:
            if pattern.search(normalized):
                intent = name
                operation = "write"
                break
        if intent == "unknown":
            for name, pattern in _READ_PATTERNS:
                if pattern.search(normalized):
                    intent = name
                    operation = "read"
                    break

    due_date = _relative_date(normalized, current)
    if operation == "write" and intent == "task.create" and due_date is None:
        flags.append("DATE_UNCLEAR")
        clarification = clarification or "未识别到明确日期；请确认办理期限后再创建事项。"
    if _SENSITIVE.search(normalized):
        flags.append("SENSITIVE_CONTENT")
        clarification = clarification or "指令可能涉及敏感业务资料，请只提供完成操作所需的最小内容。"
    if intent == "unknown" and clarification is None:
        flags.append("AMBIGUOUS")
        clarification = "没有识别到明确目标，请说明要查找内容，还是创建、修改、删除或导出。"

    requires_confirmation = operation == "write"
    confidence = 0.9 if intent != "unknown" and not flags else 0.45
    return {
        "engine": "rules",
        "intent": intent,
        "operation": operation,
        "confidence": confidence,
        "requires_confirmation": requires_confirmation,
        "can_execute": False,
        "clarification": clarification,
        "flags": sorted(set(flags)),
        "preview": {
            "title": normalized[:120],
            "due_date": due_date,
            "source_text": normalized,
        },
        "notice": "这只是结构化预览；任何写操作都必须由用户确认，并由 PartyOps 服务端重新校验权限。",
    }
