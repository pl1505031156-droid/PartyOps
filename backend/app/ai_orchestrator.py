"""PartyOps 全系统智能编排器。

本模块把模型输出限制在一组固定的业务工具契约中。模型只负责提出结构化
计划，不能生成任意函数、SQL、Shell、URL 或文件路径；真正的权限、版本、
风险和用户确认检查始终由服务端完成。没有可用的本地规划模型时，使用同
一份工具契约驱动的规则计划，保证智能能力降级不会阻塞业务。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .enums import ModelPackStatus
from .local_ai import complete_locally, llm_runtime
from .model_packs import active_model_pack, verify_installed_pack
from .models import AIModelPack, AIOrchestrationStep, User, utcnow
from .needle_intent import preview_intent_with_needle
from .problems import ProblemException


@dataclass(frozen=True)
class ToolContract:
    """可被编排器调用的最小工具契约。"""

    name: str
    description: str
    argument_keys: frozenset[str]
    risk: str
    mutates: bool
    sensitive: bool = False
    roles: frozenset[str] = frozenset({"admin", "staff"})


# 工具名称是源码白名单，不接受模型拼接出来的函数名。新增工具必须同时补
# 充参数、权限、风险、幂等和补偿测试，避免把自然语言直接变成副作用。
TOOL_REGISTRY: dict[str, ToolContract] = {
    "work.search": ToolContract(
        "work.search", "查询事项、日历和通知", frozenset({"query"}), "low", False
    ),
    "navigation.find": ToolContract(
        "navigation.find", "定位系统功能入口", frozenset({"target"}), "low", False
    ),
    "business_meeting.create_draft": ToolContract(
        "business_meeting.create_draft",
        "创建党委会或党建业务会议草稿",
        frozenset({"title", "scheduled_at", "topic_count"}),
        "medium",
        True,
    ),
    "business_meeting.prepare_workflow": ToolContract(
        "business_meeting.prepare_workflow",
        "生成会议筹备六步流程草稿",
        frozenset({"meeting_id", "responsible_ids"}),
        "medium",
        True,
    ),
    "ledger.inspect": ToolContract(
        "ledger.inspect", "检查并预览本地台账字段", frozenset({"target_type"}), "low", False
    ),
    "ledger.commit": ToolContract(
        "ledger.commit", "提交已确认的台账导入", frozenset({"import_id"}), "medium", True
    ),
    "party_development.timeline": ToolContract(
        "party_development.timeline",
        "生成发展党员真实进度时间轴草稿",
        frozenset({"case_id"}),
        "medium",
        True,
        sensitive=True,
    ),
    "notifications.recalculate": ToolContract(
        "notifications.recalculate", "按任务日期立即重算提醒", frozenset({"task_id"}), "medium", True
    ),
    "files.explain_open": ToolContract(
        "files.explain_open", "解释文件授权、预览和打开状态", frozenset({"file_id"}), "low", False
    ),
    "fleet.diagnose": ToolContract(
        "fleet.diagnose", "检查主机和协同设备健康状态", frozenset({"device_id"}), "low", False
    ),
    "fleet.rebind": ToolContract(
        "fleet.rebind", "重新绑定协同设备", frozenset({"device_id"}), "high", True, sensitive=True
    ),
    "user.archive": ToolContract(
        "user.archive", "归档用户并预览责任移交影响", frozenset({"user_id", "handover_to"}), "high", True
    ),
    "user.delete": ToolContract(
        "user.delete", "删除用户数据（仅允许进入归档流程）", frozenset({"user_id"}), "high", True
    ),
    "settings.network_change": ToolContract(
        "settings.network_change", "修改协同公布地址", frozenset({"advertise_host", "port"}), "high", True, sensitive=True, roles=frozenset({"admin"})
    ),
}

_WRITE_TOOLS = frozenset(name for name, contract in TOOL_REGISTRY.items() if contract.mutates)
_DANGEROUS_MARKERS = re.compile(
    r"(?:rm\s+-rf|powershell|cmd(?:\.exe)?|/bin/(?:sh|bash)|drop\s+table|\bselect\s+.+\s+from|https?://|file://)",
    re.IGNORECASE,
)
_SAFE_OBJECT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def tool_contracts() -> list[dict[str, Any]]:
    """返回前端可展示的白名单契约，不暴露内部路径或模型提示。"""

    return [
        {
            "name": item.name,
            "description": item.description,
            "arguments": sorted(item.argument_keys),
            "risk": item.risk,
            "mutates": item.mutates,
            "sensitive": item.sensitive,
            "roles": sorted(item.roles),
            "requires_confirmation": item.mutates or item.risk == "high",
        }
        for item in TOOL_REGISTRY.values()
    ]


def _safe_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    text = _DANGEROUS_MARKERS.sub("[已隐藏]", text)
    text = re.sub(r"(?i)(密码|口令|token|密钥|secret)\s*[:=：]\s*\S+", r"\1=[已隐藏]", text)
    text = re.sub(r"(?i)[A-Za-z]:\\[^\s]+|/(?:home|root|Users|opt)/[^\s]+", "[本机路径]", text)
    return text[:limit]


def goal_summary(goal: str) -> str:
    """生成可审计但不保存原始提示的摘要。"""

    return _safe_text(goal, 500)


def input_digest(goal: str) -> str:
    return hashlib.sha256(goal.encode("utf-8")).hexdigest()


def sanitize_scope(scope: Mapping[str, Any] | None) -> dict[str, Any]:
    """只接收业务对象 ID，不接收正文、路径、凭据或任意上下文。"""

    allowed = {"task_ids", "file_ids", "meeting_ids", "case_ids", "archive_ids", "target_type"}
    result: dict[str, Any] = {}
    for key in allowed:
        value = (scope or {}).get(key)
        if key == "target_type":
            if value is not None:
                result[key] = _safe_text(value, 48)
            continue
        if isinstance(value, list):
            result[key] = [
                str(item)
                for item in value[:50]
                if item and _SAFE_OBJECT_ID.fullmatch(str(item))
            ]
    return result


def step_scope_digest(step: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {
            "tool": step.get("tool") or step.get("tool_name"),
            "arguments": step.get("arguments", {}),
            "risk": step.get("risk") or step.get("risk_level"),
            "requires_confirmation": bool(step.get("requires_confirmation", True)),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _rule_steps(goal: str) -> list[dict[str, Any]]:
    text = goal.lower()
    steps: list[dict[str, Any]] = []

    def add(tool: str, arguments: dict[str, Any], reason: str, confidence: float = 0.88) -> None:
        contract = TOOL_REGISTRY[tool]
        steps.append(
            {
                "tool": tool,
                "arguments": {key: _safe_text(value, 160) for key, value in arguments.items()},
                "reason": reason,
                "confidence": confidence,
                "evidence": [{"source": "用户目标摘要", "digest": input_digest(goal)}],
                "risk": contract.risk,
                "requires_confirmation": contract.mutates or contract.risk == "high",
            }
        )

    if any(token in text for token in ("党委会", "会议议程", "会议记录", "三会一课", "主题党日")):
        add("business_meeting.create_draft", {"title": goal_summary(goal)}, "识别到会议或党建业务筹备目标")
        add("business_meeting.prepare_workflow", {}, "为会议生成固定筹备流程，等待负责人确认", 0.84)
    if any(token in text for token in ("台账", "表格", "导入", "xlsx", "xls", "csv", "档案")):
        add("ledger.inspect", {"target_type": "重要档案"}, "识别到台账或档案导入目标")
    if "发展党员" in text or "入党" in text:
        add("party_development.timeline", {}, "识别到发展党员时间轴或节点测算目标", 0.86)
    if any(token in text for token in ("提醒", "通知", "改期", "截止", "日期")):
        add("notifications.recalculate", {}, "识别到日期或提醒联动目标", 0.82)
    if any(token in text for token in ("协同", "主机", "设备", "入网", "重绑", "证书", "端口")):
        add("fleet.diagnose", {}, "识别到主机或协同设备诊断目标")
    if any(token in text for token in ("删除用户", "归档用户", "移交责任")):
        add("user.archive", {}, "涉及用户生命周期，必须先预览责任移交并确认", 0.9)
    if any(token in text for token in ("网络地址", "公布地址", "ip地址", "ip")):
        add("settings.network_change", {}, "涉及协同公布地址变更，必须管理员单独确认", 0.8)
    if not steps:
        if any(token in text for token in ("哪里", "怎么打开", "入口", "进入")):
            add("navigation.find", {"target": goal_summary(goal)}, "定位功能入口")
        else:
            add("work.search", {"query": goal_summary(goal)}, "使用工作台范围内的只读查询")
    return steps


def _extract_json(text: str) -> dict[str, Any] | None:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE | re.DOTALL)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"\{.*\}", value, flags=re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _validate_model_plan(payload: dict[str, Any], goal: str) -> list[dict[str, Any]] | None:
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > 20:
        return None
    result: list[dict[str, Any]] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            return None
        name = raw.get("tool")
        contract = TOOL_REGISTRY.get(str(name))
        arguments = raw.get("arguments", {})
        if contract is None or not isinstance(arguments, dict):
            return None
        if set(arguments) - contract.argument_keys or _DANGEROUS_MARKERS.search(json.dumps(arguments, ensure_ascii=False)):
            return None
        safe_arguments = {key: _safe_text(value, 160) for key, value in arguments.items() if value is not None}
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if not 0 <= confidence <= 1 or confidence < 0.65:
            return None
        result.append(
            {
                "tool": contract.name,
                "arguments": safe_arguments,
                "reason": _safe_text(raw.get("reason", "模型建议的结构化步骤"), 240),
                "confidence": confidence,
                "evidence": [{"source": "用户目标摘要", "digest": input_digest(goal)}],
                "risk": contract.risk,
                "requires_confirmation": contract.mutates or contract.risk == "high",
            }
        )
    return result


def _signed_qwen_fallback(db: Session) -> AIModelPack | None:
    """查找已安装且验签通过的 Qwen3 0.6B 系统回退包。

    该包只会在已启用的主模型明确失败后使用；未签名、损坏、架构不符或
    管理员已卸载的包都不会被自动加载。
    """

    packs = db.scalars(
        select(AIModelPack).where(
            AIModelPack.model_id.in_(("qwen3-0.6b-q8_0", "qwen3-0.6b-gguf", "qwen3-0.6b")),
            AIModelPack.signature_valid.is_(True),
            AIModelPack.status.in_((ModelPackStatus.INSTALLED, ModelPackStatus.ACTIVE)),
        )
    ).all()
    for pack in packs:
        if "llm" in (pack.capabilities or []) and verify_installed_pack(pack):
            return pack
    return None


def build_plan(db: Session, user: User, goal: str, scope: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """优先使用 DeepSeek 规划，失败或不可用时安全回退到规则计划。"""

    route = preview_intent_with_needle(db, goal)
    route_flags = set(route.get("flags") or [])
    if route_flags & {"PROMPT_INJECTION", "NEGATED"}:
        # 安全路由已识别绕过/否定表达时，不再把原目标交给生成模型，也不
        # 产生任何可写步骤。用户可修改为明确、肯定的业务目标后重新规划。
        safe_step = {
            "tool": "work.search",
            "arguments": {"query": "安全路由已拦截当前请求"},
            "reason": str(route.get("clarification") or "请求未通过安全路由")[:240],
            "confidence": 1.0,
            "evidence": [{"source": "Needle 安全路由", "digest": input_digest(goal)}],
            "risk": "low",
            "requires_confirmation": False,
        }
        return {
            "model_id": "rules",
            "engine": "rules",
            "safety_router": str(route.get("engine", "rules")),
            "steps": [safe_step],
            "unresolved": [{"tool": "work.search", "reason": safe_step["reason"]}],
            "sensitive_scope": [],
            "can_execute": False,
        }

    rules = _rule_steps(goal)
    model_id = "rules"
    pack = active_model_pack(db, "llm")
    if pack and str(pack.model_id).lower().startswith(("deepseek-r1", "deepseek", "qwen3-0.6")):
        instruction = (
            "请只输出 JSON，不要 Markdown。你是 PartyOps 计划器，不能执行任何操作。\n"
            "只能从下列固定工具中选择：" + ", ".join(sorted(TOOL_REGISTRY)) + "。\n"
            "输出格式：{\"steps\":[{\"tool\":\"…\",\"arguments\":{},\"reason\":\"…\",\"confidence\":0.0}],\"unresolved\":[]}。\n"
            "所有写入、删除、归档、发送、权限、设备和网络操作必须要求确认；不得把预测当作事实。\n"
            f"用户目标：{goal_summary(goal)}"
        )
        try:
            candidate = _extract_json(complete_locally(db, instruction, []))
            validated = _validate_model_plan(candidate, goal) if candidate else None
            if validated:
                rules = validated
                model_id = str(pack.model_id)
        except Exception:
            # DeepSeek 不可用时，只允许使用已经正式验签并安装的指定低配
            # 回退包；仍失败则落到规则引擎，绝不调用外部模型。
            fallback = _signed_qwen_fallback(db)
            if fallback and fallback.id != pack.id:
                try:
                    candidate = _extract_json(llm_runtime.complete(fallback, instruction, []))
                    validated = _validate_model_plan(candidate, goal) if candidate else None
                    if validated:
                        rules = validated
                        model_id = str(fallback.model_id)
                except Exception:
                    model_id = "rules"
            else:
                model_id = "rules"
    unresolved = []
    for item in rules:
        if item["requires_confirmation"]:
            unresolved.append({"tool": item["tool"], "reason": "执行前需要用户确认并重新校验权限"})
    return {
        "model_id": model_id,
        "engine": (
            "deepseek"
            if model_id.lower().startswith("deepseek")
            else "qwen"
            if model_id.lower().startswith("qwen")
            else "rules"
        ),
        "safety_router": str(route.get("engine", "rules")),
        "steps": rules,
        "unresolved": unresolved,
        "sensitive_scope": [key for key in sanitize_scope(scope) if key in {"file_ids", "case_ids", "archive_ids"}],
        "can_execute": False,
    }


def active_planner_info(db: Session) -> dict[str, Any]:
    pack = active_model_pack(db, "llm")
    if pack and str(pack.model_id).lower().startswith("deepseek"):
        return {"model_id": pack.model_id, "role": "主编排模型", "state": "active"}
    if pack and str(pack.model_id).lower().startswith(("qwen3-0.6", "qwen3_0.6")):
        return {"model_id": pack.model_id, "role": "低配回退模型", "state": "active"}
    return {"model_id": "rules", "role": "规则编排引擎", "state": "fallback"}


def get_owned_step(db: Session, session_id: str, step_id: str, user: User) -> tuple[Any, AIOrchestrationStep]:
    from .models import AIOrchestrationSession

    session = db.get(AIOrchestrationSession, session_id)
    step = db.get(AIOrchestrationStep, step_id)
    if not session or session.user_id != user.id or not step or step.session_id != session.id:
        raise ProblemException(404, "AI_ORCHESTRATION_NOT_FOUND", "编排会话不存在", "请刷新后重试。")
    return session, step


def ensure_session_active(session: Any) -> None:
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        # SQLite DateTime 列不保留时区；本项目内部约定无时区值为 UTC。
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        session.state = "expired"
        raise ProblemException(410, "AI_ORCHESTRATION_EXPIRED", "编排会话已过期", "请重新描述目标生成计划。")
    if session.state in {"cancelled", "completed", "failed", "expired"}:
        raise ProblemException(409, "AI_ORCHESTRATION_CLOSED", "编排会话已结束", "请重新规划或查看审计记录。")


_BUSINESS_HANDOFFS: dict[str, dict[str, str]] = {
    "business_meeting.create_draft": {"route": "/business-meetings", "label": "打开会议草稿"},
    "business_meeting.prepare_workflow": {"route": "/business-meetings", "label": "打开会议筹备流程"},
    "ledger.commit": {"route": "/archives", "label": "打开台账导入"},
    "party_development.timeline": {"route": "/party-development?tab=cases", "label": "打开发展党员时间轴"},
    "notifications.recalculate": {"route": "/notifications", "label": "打开通知重算"},
    "fleet.rebind": {"route": "/fleet/devices", "label": "打开设备重绑"},
    "user.archive": {"route": "/settings/updates?tab=users", "label": "打开用户归档"},
    "user.delete": {"route": "/settings/updates?tab=users", "label": "打开用户生命周期"},
    "settings.network_change": {"route": "/fleet/grants", "label": "打开网络与协同"},
}


def dispatch_step(step: AIOrchestrationStep, contract: ToolContract) -> dict[str, Any]:
    """固定工具的安全执行边界。

    写操作绝不在模型进程中执行，而是生成一个固定、同源的业务办理入口；
    用户进入对应页面后仍需接受该模块自己的权限、版本、参数与二次确认
    校验。编排步骤在业务写入完成前保持 ``awaiting_business_action``，不能
    虚报为“已完成”。只读工具可以在当前会话内完成。
    """

    if contract.mutates:
        handoff = _BUSINESS_HANDOFFS[contract.name]
        return {
            "status": "action_required",
            "tool": contract.name,
            "preview_only": True,
            "handoff": handoff,
            "message": "已通过编排器确认门禁；请在对应业务页面核对并完成实际写入。",
        }
    return {"status": "completed", "tool": contract.name, "preview_only": False}


__all__ = [
    "TOOL_REGISTRY",
    "ToolContract",
    "active_planner_info",
    "build_plan",
    "dispatch_step",
    "ensure_session_active",
    "get_owned_step",
    "goal_summary",
    "input_digest",
    "sanitize_scope",
    "step_scope_digest",
    "tool_contracts",
]
