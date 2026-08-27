"""AI 配置、权限策略、连通性测试和只读草稿。"""

from __future__ import annotations

import json
import secrets
import typing
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_orchestrator import (
    TOOL_REGISTRY,
    active_planner_info,
    build_plan,
    dispatch_step,
    ensure_session_active,
    goal_summary,
    input_digest,
    sanitize_scope,
    step_scope_digest,
    tool_contracts,
)
from ..ai_service import (
    call_compatible_model,
    collect_sources,
    encrypt_api_key,
    is_private_endpoint,
    provider_output,
    test_provider,
    validate_provider_url,
)
from ..audit import emit_event, write_audit
from ..config import get_settings
from ..database import get_session
from ..enums import RecommendationStatus
from ..hardware_profile import collect_hardware_profile, run_light_benchmark
from ..local_ai import (
    complete_locally,
    embedding_runtime,
    llm_runtime,
    local_runtime_status,
)
from ..model_catalog import recommend_models
from ..model_packs import (
    activate_model_pack,
    cleanup_model_pack_uninstall_staging,
    deactivate_model_capability,
    finish_staged_model_pack_removal,
    install_model_pack,
    normalized_architecture,
    remove_installed_pack_files,
    rollback_staged_model_pack_removal,
    stage_model_pack_removal,
)
from ..models import (
    AIContextGrant,
    AIDraft,
    AIInvocation,
    AIModelActivation,
    AIModelPack,
    AIOrchestrationApproval,
    AIOrchestrationEvent,
    AIOrchestrationSession,
    AIOrchestrationStep,
    AIPolicy,
    AIProviderConfig,
    AIRecommendation,
    User,
    utcnow,
)
from ..needle_intent import needle_intent_runtime, preview_intent_with_needle
from ..problems import ProblemException
from ..recommendations import list_recommendations
from ..schemas import (
    AIDraftOut,
    AIModelPackOut,
    AIOrchestrationApprovalRequest,
    AIOrchestrationCapabilitiesOut,
    AIOrchestrationCreate,
    AIOrchestrationOut,
    AIOrchestrationReplan,
    AIPolicyOut,
    AIPolicyPatch,
    AIProviderOut,
    AIProviderPatch,
    AIQueryRequest,
    AIRecommendationOut,
    HardwareBenchmarkOut,
    HardwareProfileOut,
    IntentPreviewOut,
    IntentPreviewRequest,
    LocalAIRuntimeOut,
    ModelRecommendationOut,
)
from ..security import get_current_user, require_admin

router = APIRouter(tags=["ai"])


def _highest_risk(steps: list[dict[str, Any]]) -> str:
    rank = {"low": 0, "medium": 1, "high": 2}
    return max(
        (str(item.get("risk", "low")) for item in steps),
        key=lambda value: rank.get(value, -1),
        default="low",
    )

MAX_MODEL_UPLOAD_BYTES = 4 * 1024**3


@router.get("/ai/hardware-profile", response_model=HardwareProfileOut)
def get_ai_hardware_profile(
    _admin: User = Depends(require_admin),
) -> HardwareProfileOut:
    return HardwareProfileOut.model_validate(collect_hardware_profile())


@router.post("/ai/hardware-profile/benchmark", response_model=HardwareBenchmarkOut)
def benchmark_ai_hardware(
    _admin: User = Depends(require_admin),
) -> HardwareBenchmarkOut:
    return HardwareBenchmarkOut.model_validate(run_light_benchmark())


@router.get("/ai/model-recommendations", response_model=typing.List[ModelRecommendationOut])
def get_model_recommendations(
    _admin: User = Depends(require_admin),
) -> list[ModelRecommendationOut]:
    profile = collect_hardware_profile()
    return [ModelRecommendationOut.model_validate(item) for item in recommend_models(profile)]


@router.post("/ai/intent/preview", response_model=IntentPreviewOut)
def get_intent_preview(
    payload: IntentPreviewRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> IntentPreviewOut:
    """只返回结构化预览，不执行任何模型提出的工具调用。"""

    return IntentPreviewOut.model_validate(preview_intent_with_needle(db, payload.text))


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def parse_version(value: str | None) -> int:
    if value is None:
        raise ProblemException(428, "IF_MATCH_REQUIRED", "缺少版本号", "修改必须携带 If-Match。")
    try:
        return int(value.strip('"'))
    except ValueError as exc:
        raise ProblemException(400, "IF_MATCH_INVALID", "版本号无效", "If-Match 必须是整数。") from exc


def _orchestration_output(db: Session, session: AIOrchestrationSession) -> AIOrchestrationOut:
    steps = list(
        db.scalars(
            select(AIOrchestrationStep)
            .where(AIOrchestrationStep.session_id == session.id)
            .order_by(AIOrchestrationStep.step_order)
        ).all()
    )
    serialized_steps: list[dict[str, Any]] = []
    for step in steps:
        serialized = {
            "id": step.id,
            "step_order": step.step_order,
            "tool_name": step.tool_name,
            "arguments": step.arguments or {},
            "reason": step.reason,
            "evidence": step.evidence or [],
            "confidence": step.confidence,
            "risk_level": step.risk_level,
            "requires_confirmation": step.requires_confirmation,
            "status": step.status,
            "result_summary": step.result_summary or {},
            "error_code": step.error_code,
            "version": step.version,
            "scope_sha256": step_scope_digest(
                {
                    "tool": step.tool_name,
                    "arguments": step.arguments or {},
                    "risk": step.risk_level,
                    "requires_confirmation": step.requires_confirmation,
                }
            ),
        }
        serialized_steps.append(serialized)
    return AIOrchestrationOut.model_validate(
        {
            "id": session.id,
            "goal_summary": session.goal_summary,
            "input_sha256": session.input_sha256,
            "state": session.state,
            "model_id": session.model_id,
            "external_consented": session.external_consented,
            "risk_level": session.risk_level,
            "context_scope": session.context_scope or {},
            "plan": session.plan or {},
            "unresolved": session.unresolved or [],
            "expires_at": session.expires_at,
            "version": session.version,
            "completed_at": session.completed_at,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "steps": serialized_steps,
        }
    )


def _get_orchestration(db: Session, session_id: str, user: User) -> AIOrchestrationSession:
    session = db.get(AIOrchestrationSession, session_id)
    if not session or session.user_id != user.id:
        raise ProblemException(404, "AI_ORCHESTRATION_NOT_FOUND", "编排会话不存在", "请刷新后重试。")
    return session


@router.get("/ai/capabilities", response_model=AIOrchestrationCapabilitiesOut)
def get_ai_orchestration_capabilities(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationCapabilitiesOut:
    """展示编排器角色分工；不返回本机路径、提示原文或密钥。"""

    del user
    return AIOrchestrationCapabilitiesOut.model_validate(
        {
            "release": "1.4.5-rc.6",
            "planner": active_planner_info(db),
            "components": [
                {
                    "id": "deepseek-r1-distill-qwen-1.5b",
                    "role": "主编排模型",
                    "state": active_planner_info(db)["state"] if active_planner_info(db)["model_id"] != "rules" else "可选签名包",
                    "description": "负责中文多步骤计划和候选解释，不直接执行操作。",
                },
                {
                    "id": "needle2-intent",
                    "role": "安全路由",
                    "state": "固定白名单",
                    "description": "识别意图、风险和可用工具，拒绝任意函数调用。",
                },
                {
                    "id": "bge-small-zh-v1.5",
                    "role": "中文语义检索",
                    "state": "可启用签名包",
                    "description": "用于表头匹配、重复检测和资料召回，不负责生成文本。",
                },
                {
                    "id": "qwen3-0.6b-q8_0",
                    "role": "低配回退",
                    "state": "可选签名包",
                    "description": "DeepSeek 不可用时提供轻量中文草稿能力。",
                },
                {
                    "id": "rules",
                    "role": "最终兜底",
                    "state": "始终可用",
                    "description": "模型失败时仍按固定规则生成安全计划。",
                },
            ],
            "tools": tool_contracts(),
            "external_models": {
                "enabled_by_default": False,
                "consent_scope": "逐会话、最小脱敏上下文、只读第二意见",
            },
        }
    )


@router.post("/ai/orchestrations", response_model=AIOrchestrationOut, status_code=201)
def create_ai_orchestration(
    payload: AIOrchestrationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    context_scope = sanitize_scope(payload.context_scope)
    plan = build_plan(db, user, payload.goal, context_scope)
    session = AIOrchestrationSession(
        user_id=user.id,
        goal_summary=goal_summary(payload.goal),
        input_sha256=input_digest(payload.goal),
        state="awaiting_confirmation",
        model_id=str(plan.get("model_id", "rules")),
        # 外部模型必须走单独的显式授权接口，不能通过创建请求隐式打开。
        external_consented=False,
        risk_level=_highest_risk(list(plan.get("steps", []))),
        context_scope=context_scope,
        plan=plan,
        unresolved=list(plan.get("unresolved", [])),
        expires_at=utcnow() + timedelta(minutes=30),
    )
    db.add(session)
    db.flush()
    for order, item in enumerate(plan.get("steps", []), start=1):
        tool_name = str(item.get("tool", ""))
        contract = TOOL_REGISTRY.get(tool_name)
        if not contract:
            # build_plan 已经做过校验，这里保留第二道防线。
            raise ProblemException(422, "AI_TOOL_NOT_ALLOWED", "编排工具未获允许", "计划包含未注册的业务工具。")
        arguments = dict(item.get("arguments") or {})
        step = AIOrchestrationStep(
            session_id=session.id,
            step_order=order,
            tool_name=tool_name,
            arguments=arguments,
            reason=str(item.get("reason", ""))[:2000],
            evidence=list(item.get("evidence") or [])[:10],
            confidence=float(item.get("confidence", 0.0)),
            risk_level=contract.risk,
            requires_confirmation=contract.mutates or contract.risk == "high",
            status="pending",
            idempotency_key=f"{session.id}:{order}:{input_digest(json.dumps(arguments, ensure_ascii=False, sort_keys=True))[:16]}",
        )
        db.add(step)
    db.add(
        AIContextGrant(
            session_id=session.id,
            user_id=user.id,
            scope=context_scope,
            expires_at=session.expires_at,
        )
    )
    db.add(
        AIOrchestrationEvent(
            session_id=session.id,
            event_type="planned",
            result_code="OK",
            summary={"model_id": session.model_id, "step_count": len(plan.get("steps", []))},
            created_by=user.id,
        )
    )
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.get("/ai/orchestrations/{session_id}", response_model=AIOrchestrationOut)
def get_ai_orchestration(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    return _orchestration_output(db, _get_orchestration(db, session_id, user))


@router.post("/ai/orchestrations/{session_id}/replan", response_model=AIOrchestrationOut)
def replan_ai_orchestration(
    session_id: str,
    payload: AIOrchestrationReplan,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    session = _get_orchestration(db, session_id, user)
    ensure_session_active(session)
    if session.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "编排计划已更新", "请刷新后重新规划。")
    goal = payload.goal or session.goal_summary
    scope = sanitize_scope(payload.context_scope if payload.context_scope is not None else session.context_scope)
    plan = build_plan(db, user, goal, scope)
    old_steps = list(db.scalars(select(AIOrchestrationStep).where(AIOrchestrationStep.session_id == session.id)).all())
    for step in old_steps:
        if step.status not in {"completed", "running"}:
            step.status = "obsolete"
            step.version += 1
    session.goal_summary = goal_summary(goal)
    session.input_sha256 = input_digest(goal)
    session.context_scope = scope
    session.plan = plan
    session.model_id = str(plan.get("model_id", "rules"))
    session.unresolved = list(plan.get("unresolved", []))
    session.risk_level = _highest_risk(list(plan.get("steps", [])))
    session.version += 1
    for order, item in enumerate(plan.get("steps", []), start=1):
        contract = TOOL_REGISTRY.get(str(item.get("tool", "")))
        if not contract:
            raise ProblemException(422, "AI_TOOL_NOT_ALLOWED", "编排工具未获允许", "计划包含未注册的业务工具。")
        arguments = dict(item.get("arguments") or {})
        db.add(AIOrchestrationStep(
            session_id=session.id,
            step_order=order,
            tool_name=contract.name,
            arguments=arguments,
            reason=str(item.get("reason", ""))[:2000],
            evidence=list(item.get("evidence") or [])[:10],
            confidence=float(item.get("confidence", 0.0)),
            risk_level=contract.risk,
            requires_confirmation=contract.mutates or contract.risk == "high",
            status="pending",
            idempotency_key=f"{session.id}:r{session.version}:{order}:{input_digest(json.dumps(arguments, ensure_ascii=False, sort_keys=True))[:16]}",
        ))
    db.add(AIOrchestrationEvent(session_id=session.id, event_type="replanned", result_code="OK", summary={"step_count": len(plan.get("steps", []))}, created_by=user.id))
    write_audit(db, user, "ai.orchestration_replan", "ai_orchestration", session.id, {"step_count": len(plan.get("steps", []))}, client_ip(request))
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.post("/ai/orchestrations/{session_id}/steps/{step_id}/approve", response_model=AIOrchestrationOut)
def approve_ai_orchestration_step(
    session_id: str,
    step_id: str,
    payload: AIOrchestrationApprovalRequest,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    session = _get_orchestration(db, session_id, user)
    ensure_session_active(session)
    if session.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "编排计划已更新", "请刷新后重新确认。")
    step = db.get(AIOrchestrationStep, step_id)
    if not step or step.session_id != session.id:
        raise ProblemException(404, "AI_ORCHESTRATION_STEP_NOT_FOUND", "编排步骤不存在", "请刷新后重试。")
    contract = TOOL_REGISTRY.get(step.tool_name)
    if not contract:
        raise ProblemException(422, "AI_TOOL_NOT_ALLOWED", "编排工具未获允许", "该步骤已被安全策略拒绝。")
    if str(getattr(user.role, "value", user.role)) not in contract.roles:
        raise ProblemException(403, "AI_TOOL_PERMISSION_DENIED", "没有执行该步骤的权限", "请由管理员完成高风险操作或调整业务权限。")
    expected_scope = step_scope_digest({"tool": step.tool_name, "arguments": step.arguments or {}, "risk": step.risk_level, "requires_confirmation": step.requires_confirmation})
    if payload.approved and payload.scope_sha256 != expected_scope:
        raise ProblemException(409, "AI_APPROVAL_SCOPE_CHANGED", "确认范围已变化", "计划内容已更新，请重新查看后确认。")
    step.status = "approved" if payload.approved else "rejected"
    step.version += 1
    session.version += 1
    db.add(AIOrchestrationApproval(session_id=session.id, step_id=step.id, user_id=user.id, approved=payload.approved, scope_sha256=expected_scope if payload.approved else "", device_id=""))
    db.add(AIOrchestrationEvent(session_id=session.id, step_id=step.id, event_type="approved" if payload.approved else "rejected", result_code="OK", summary={"tool": contract.name}, created_by=user.id))
    write_audit(db, user, "ai.orchestration_step_approve" if payload.approved else "ai.orchestration_step_reject", "ai_orchestration_step", step.id, {"tool": contract.name}, client_ip(request))
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.post("/ai/orchestrations/{session_id}/execute", response_model=AIOrchestrationOut)
def execute_ai_orchestration(
    session_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    session = _get_orchestration(db, session_id, user)
    ensure_session_active(session)
    if session.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "编排计划已更新", "请刷新后重试。")
    steps = list(db.scalars(select(AIOrchestrationStep).where(AIOrchestrationStep.session_id == session.id).order_by(AIOrchestrationStep.step_order)).all())
    pending = [step for step in steps if step.status in {"pending", "rejected"} and step.requires_confirmation]
    if pending:
        raise ProblemException(409, "AI_CONFIRMATION_REQUIRED", "仍有步骤未确认", "请逐项确认写入、归档、删除或网络变更步骤。", extra={"step_ids": [step.id for step in pending]})
    session.state = "running"
    session.version += 1
    for step in steps:
        if step.status in {"completed", "rejected", "obsolete", "awaiting_business_action"}:
            continue
        contract = TOOL_REGISTRY.get(step.tool_name)
        if not contract:
            step.status = "failed"
            step.error_code = "AI_TOOL_NOT_ALLOWED"
            session.state = "failed"
            break
        if str(getattr(user.role, "value", user.role)) not in contract.roles:
            step.status = "failed"
            step.error_code = "AI_TOOL_PERMISSION_DENIED"
            session.state = "failed"
            break
        step.status = "running"
        try:
            result = dispatch_step(step, contract)
            step.result_summary = result
            requires_business_action = result.get("status") == "action_required"
            step.status = "awaiting_business_action" if requires_business_action else "completed"
            step.version += 1
            db.add(
                AIOrchestrationEvent(
                    session_id=session.id,
                    step_id=step.id,
                    event_type="step_handoff_created" if requires_business_action else "step_completed",
                    result_code="BUSINESS_ACTION_REQUIRED" if requires_business_action else "OK",
                    summary={"tool": contract.name, "preview_only": bool(result.get("preview_only"))},
                    created_by=user.id,
                )
            )
        except Exception:
            step.status = "failed"
            step.error_code = "AI_STEP_EXECUTION_FAILED"
            session.state = "failed"
            db.add(AIOrchestrationEvent(session_id=session.id, step_id=step.id, event_type="step_failed", result_code=step.error_code, summary={}, created_by=user.id))
            break
    if session.state == "running":
        if any(step.status == "awaiting_business_action" for step in steps):
            session.state = "awaiting_business_action"
        else:
            session.state = "completed"
            session.completed_at = utcnow()
    write_audit(db, user, "ai.orchestration_execute", "ai_orchestration", session.id, {"state": session.state}, client_ip(request))
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.post("/ai/orchestrations/{session_id}/cancel", response_model=AIOrchestrationOut)
def cancel_ai_orchestration(
    session_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    session = _get_orchestration(db, session_id, user)
    if session.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "编排计划已更新", "请刷新后重试。")
    if session.state in {"completed", "failed", "cancelled"}:
        raise ProblemException(409, "AI_ORCHESTRATION_CLOSED", "编排会话已结束", "不能重复取消。")
    session.state = "cancelled"
    session.version += 1
    db.add(AIOrchestrationEvent(session_id=session.id, event_type="cancelled", result_code="OK", summary={}, created_by=user.id))
    write_audit(db, user, "ai.orchestration_cancel", "ai_orchestration", session.id, {}, client_ip(request))
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.get(
    "/ai/orchestrations/{session_id}/audit",
    response_model=typing.List[typing.Dict[str, Any]],
)
def audit_ai_orchestration(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    session = _get_orchestration(db, session_id, user)
    events = list(db.scalars(select(AIOrchestrationEvent).where(AIOrchestrationEvent.session_id == session.id).order_by(AIOrchestrationEvent.created_at)).all())
    return [{"id": event.id, "step_id": event.step_id, "event_type": event.event_type, "result_code": event.result_code, "summary": event.summary or {}, "created_at": event.created_at} for event in events]


@router.post("/ai/orchestrations/{session_id}/external-consent", response_model=AIOrchestrationOut)
def grant_ai_external_consent(
    session_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIOrchestrationOut:
    session = _get_orchestration(db, session_id, user)
    ensure_session_active(session)
    if session.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "编排会话已更新", "请刷新后重试。")
    # 这里只记录逐会话授权，不自动发送数据；外部模型调用仍需经过
    # 脱敏上下文构造和现有 AI 外发确认流程。
    session.external_consented = True
    session.version += 1
    db.add(AIOrchestrationEvent(session_id=session.id, event_type="external_consent", result_code="OK", summary={"scope": "脱敏目标与字段候选"}, created_by=user.id))
    write_audit(db, user, "ai.orchestration_external_consent", "ai_orchestration", session.id, {"scope": "redacted"}, client_ip(request))
    db.commit()
    db.refresh(session)
    return _orchestration_output(db, session)


@router.get("/admin/ai/model-packs", response_model=typing.List[AIModelPackOut])
def list_model_packs(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AIModelPackOut]:
    cleanup_model_pack_uninstall_staging()
    active_by_pack: dict[str, list[str]] = {}
    for activation in db.scalars(select(AIModelActivation)).all():
        active_by_pack.setdefault(activation.model_pack_id, []).append(activation.capability)
    return [
        AIModelPackOut.model_validate(pack).model_copy(
            update={"active_capabilities": sorted(active_by_pack.get(pack.id, []))}
        )
        for pack in db.scalars(select(AIModelPack).order_by(AIModelPack.created_at.desc())).all()
    ]


@router.post("/admin/ai/model-packs", response_model=AIModelPackOut, status_code=201)
async def upload_model_pack(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIModelPackOut:
    safe_name = PurePosixPath(file.filename or "partyops.partyops-modelpack").name
    if not safe_name.endswith(".partyops-modelpack"):
        raise ProblemException(422, "MODEL_PACK_EXTENSION_INVALID", "模型包格式不正确", "请选择 .partyops-modelpack 文件。")
    upload_dir = get_settings().models_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"upload-{secrets.token_hex(8)}.partyops-modelpack"
    size = 0
    pack: AIModelPack | None = None
    committed = False
    try:
        with path.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_MODEL_UPLOAD_BYTES:
                    raise ProblemException(413, "MODEL_PACK_TOO_LARGE", "模型包超过4GB限制", "请重新获取精简模型包。")
                handle.write(chunk)
        pack = install_model_pack(path, safe_name, admin, db)
        write_audit(
            db,
            admin,
            "ai.model_pack_import",
            "ai_model_pack",
            pack.id,
            {"model_id": pack.model_id, "version": pack.version, "signature_valid": pack.signature_valid},
            client_ip(request),
        )
        db.commit()
        committed = True
        db.refresh(pack)
        return AIModelPackOut.model_validate(pack)
    except Exception:
        db.rollback()
        if pack is not None and not committed:
            remove_installed_pack_files(pack)
        path.unlink(missing_ok=True)
        raise


@router.post("/admin/ai/model-packs/{pack_id}/activate", response_model=AIModelPackOut)
def activate_local_model_pack(
    pack_id: str,
    request: Request,
    capability: str = Query(pattern=r"^(embedding|llm|intent_router)$"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIModelPackOut:
    pack = db.get(AIModelPack, pack_id)
    if not pack:
        raise ProblemException(404, "MODEL_PACK_NOT_FOUND", "模型包不存在", "请刷新模型包列表。")
    if pack.architecture not in {"universal", normalized_architecture()}:
        raise ProblemException(409, "MODEL_PACK_ARCH_MISMATCH", "模型包架构不匹配", f"本机需要 {normalized_architecture()} 模型包。")
    if capability == "llm":
        llm_runtime.stop()
    elif capability == "embedding":
        embedding_runtime.unload()
    elif capability == "intent_router":
        try:
            needle_intent_runtime.probe(pack)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProblemException(
                409,
                "NEEDLE_RUNTIME_INVALID",
                "Needle 意图运行时不可用",
                "原生库与当前平台不匹配、组件损坏或缺少必需接口。",
            ) from exc
    pack = activate_model_pack(db, pack, capability, admin.id)
    write_audit(db, admin, "ai.model_pack_activate", "ai_model_pack", pack.id, {"model_id": pack.model_id, "capability": capability}, client_ip(request))
    emit_event(db, "ai.model_pack_activated", pack.id, {"capability": capability})
    db.commit()
    db.refresh(pack)
    active = list(
        db.scalars(
            select(AIModelActivation.capability).where(AIModelActivation.model_pack_id == pack.id)
        ).all()
    )
    return AIModelPackOut.model_validate(pack).model_copy(update={"active_capabilities": sorted(active)})


@router.delete("/admin/ai/model-activations/{capability}", response_model=dict)
def deactivate_local_model_capability(
    capability: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict:
    if capability not in {"embedding", "llm", "intent_router"}:
        raise ProblemException(422, "MODEL_CAPABILITY_INVALID", "模型能力无效", "请选择 embedding、llm 或 intent_router。")
    if capability == "llm":
        llm_runtime.stop()
    elif capability == "embedding":
        embedding_runtime.unload()
    elif capability == "intent_router":
        needle_intent_runtime.unload()
    pack = deactivate_model_capability(db, capability)
    write_audit(
        db,
        admin,
        "ai.model_capability_deactivate",
        "ai_model_pack",
        pack.id if pack else capability,
        {"capability": capability},
        client_ip(request),
    )
    db.commit()
    return {"capability": capability, "active": False, "pack_id": pack.id if pack else None}


@router.delete("/admin/ai/model-packs/{pack_id}", response_model=dict)
def uninstall_local_model_pack(
    pack_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> dict[str, object]:
    """卸载未启用的签名包；业务数据与历史调用记录不受影响。"""

    pack = db.get(AIModelPack, pack_id)
    if not pack:
        raise ProblemException(404, "MODEL_PACK_NOT_FOUND", "模型包不存在", "请刷新模型包列表。")
    active = list(
        db.scalars(
            select(AIModelActivation.capability).where(
                AIModelActivation.model_pack_id == pack.id
            )
        ).all()
    )
    if active:
        raise ProblemException(
            409,
            "MODEL_PACK_ACTIVE",
            "模型包仍在使用",
            "请先停用该模型包的全部能力，再执行卸载。",
            extra={"active_capabilities": sorted(active)},
        )
    try:
        stage_root, moves = stage_model_pack_removal(pack)
    except OSError as exc:
        raise ProblemException(
            409,
            "MODEL_PACK_FILES_BUSY",
            "模型文件仍被系统占用",
            "请确认全部模型能力已停用，重新启动 PartyOps 后再试。",
        ) from exc
    try:
        write_audit(
            db,
            admin,
            "ai.model_pack_uninstall",
            "ai_model_pack",
            pack.id,
            {"model_id": pack.model_id, "version": pack.version},
            client_ip(request),
        )
        db.delete(pack)
        db.commit()
    except Exception:
        db.rollback()
        rollback_staged_model_pack_removal(stage_root, moves)
        raise
    cleanup_pending = finish_staged_model_pack_removal(stage_root)
    return {
        "id": pack_id,
        "uninstalled": True,
        "cleanup_pending": cleanup_pending,
    }


@router.get("/ai/runtime/status", response_model=LocalAIRuntimeOut)
def get_local_ai_status(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> LocalAIRuntimeOut:
    return LocalAIRuntimeOut.model_validate(local_runtime_status(db))


@router.get("/ai/recommendations", response_model=typing.List[AIRecommendationOut])
def get_ai_recommendations(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIRecommendation]:
    items = list_recommendations(db, user, limit)
    db.commit()
    return items


def _handle_recommendation(
    recommendation_id: str,
    target: RecommendationStatus,
    request: Request,
    if_match: str | None,
    user: User,
    db: Session,
) -> AIRecommendation:
    item = db.get(AIRecommendation, recommendation_id)
    if not item or item.user_id != user.id:
        raise ProblemException(404, "AI_RECOMMENDATION_NOT_FOUND", "智能建议不存在", "该建议可能已经过期。")
    if item.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "智能建议已更新", "请刷新后重试。")
    if item.status != RecommendationStatus.PENDING:
        raise ProblemException(409, "AI_RECOMMENDATION_HANDLED", "智能建议已处理", "该建议不能重复操作。")
    item.status = target
    item.version += 1
    write_audit(db, user, f"ai.recommendation_{target.value}", "ai_recommendation", item.id, {"generator": item.generator.value}, client_ip(request))
    db.commit()
    db.refresh(item)
    return item


@router.post("/ai/recommendations/{recommendation_id}/accept", response_model=AIRecommendationOut)
def accept_ai_recommendation(
    recommendation_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIRecommendation:
    return _handle_recommendation(recommendation_id, RecommendationStatus.ACCEPTED, request, if_match, user, db)


@router.post("/ai/recommendations/{recommendation_id}/dismiss", response_model=AIRecommendationOut)
def dismiss_ai_recommendation(
    recommendation_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIRecommendation:
    return _handle_recommendation(recommendation_id, RecommendationStatus.DISMISSED, request, if_match, user, db)


@router.get("/ai/settings", response_model=AIProviderOut)
def get_ai_settings(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    return AIProviderOut.model_validate(provider_output(provider))


@router.patch("/ai/settings", response_model=AIProviderOut)
def patch_ai_settings(
    payload: AIProviderPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    if provider and provider.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 配置已更新", "请刷新后重试。")
    if payload.enabled and (not payload.base_url.strip() or not payload.model.strip()):
        raise ProblemException(422, "AI_CONFIG_INCOMPLETE", "AI 配置不完整", "启用前必须填写接口地址和模型名称。")
    if payload.base_url:
        validate_provider_url(
            payload.base_url.strip(),
            payload.trusted_intranet,
            resolve=False,
        )
    is_new = provider is None
    if is_new:
        provider = AIProviderConfig(created_by=admin.id, version=1)
        db.add(provider)
    provider.name = payload.name.strip()
    provider.base_url = payload.base_url.strip()
    provider.model = payload.model.strip()
    provider.enabled = payload.enabled
    provider.trusted_intranet = payload.trusted_intranet
    provider.timeout_seconds = payload.timeout_seconds
    if payload.api_key is not None:
        provider.api_key_encrypted = (
            encrypt_api_key(payload.api_key) if payload.api_key else ""
        )
    if not is_new:
        provider.version += 1
    db.flush()
    write_audit(
        db,
        admin,
        "ai.settings_update",
        "ai_provider",
        provider.id,
        {
            "enabled": provider.enabled,
            "model": provider.model,
            "private_endpoint": is_private_endpoint(provider.base_url),
            "has_api_key": bool(provider.api_key_encrypted),
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(provider)
    return AIProviderOut.model_validate(provider_output(provider))


@router.post("/ai/settings/test", response_model=AIProviderOut)
def test_ai_settings(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIProviderOut:
    provider = db.scalar(select(AIProviderConfig).order_by(AIProviderConfig.created_at))
    if not provider or not provider.base_url:
        raise ProblemException(409, "AI_NOT_CONFIGURED", "尚未配置 AI", "请先保存模型服务地址。")
    try:
        test_provider(provider)
        provider.last_status = "connected"
        provider.last_error = ""
    except ProblemException as exc:
        provider.last_status = "failed"
        provider.last_error = exc.code
        provider.last_test_at = utcnow()
        db.commit()
        raise
    provider.last_test_at = utcnow()
    write_audit(db, admin, "ai.connection_test", "ai_provider", provider.id, {"status": provider.last_status}, client_ip(request))
    db.commit()
    return AIProviderOut.model_validate(provider_output(provider))


@router.get("/ai/policies", response_model=typing.List[AIPolicyOut])
def list_ai_policies(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[AIPolicy]:
    return list(db.scalars(select(AIPolicy).order_by(AIPolicy.created_at)).all())


@router.post("/ai/policies", response_model=AIPolicyOut, status_code=201)
def create_ai_policy(
    payload: AIPolicyPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIPolicy:
    policy = AIPolicy(
        name=payload.name.strip(),
        allowed_root_ids=payload.allowed_root_ids,
        allowed_task_categories=payload.allowed_task_categories,
        allowed_file_types=payload.allowed_file_types,
        capabilities=[item.value for item in payload.capabilities],
        allow_restricted=False,
        active=payload.active,
        created_by=admin.id,
    )
    db.add(policy)
    db.flush()
    write_audit(
        db,
        admin,
        "ai.policy_create",
        "ai_policy",
        policy.id,
        {
            "roots": len(policy.allowed_root_ids),
            "capabilities": policy.capabilities,
            "allow_restricted": False,
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.patch("/ai/policies/{policy_id}", response_model=AIPolicyOut)
def patch_ai_policy(
    policy_id: str,
    payload: AIPolicyPatch,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> AIPolicy:
    policy = db.get(AIPolicy, policy_id)
    if not policy:
        raise ProblemException(404, "AI_POLICY_NOT_FOUND", "AI 权限策略不存在", "未找到该策略。")
    if policy.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 权限策略已更新", "请刷新后重试。")
    policy.name = payload.name.strip()
    policy.allowed_root_ids = payload.allowed_root_ids
    policy.allowed_task_categories = payload.allowed_task_categories
    policy.allowed_file_types = payload.allowed_file_types
    policy.capabilities = [item.value for item in payload.capabilities]
    policy.allow_restricted = False
    policy.active = payload.active
    policy.version += 1
    write_audit(
        db,
        admin,
        "ai.policy_update",
        "ai_policy",
        policy.id,
        {
            "roots": len(policy.allowed_root_ids),
            "capabilities": policy.capabilities,
            "allow_restricted": False,
        },
        client_ip(request),
    )
    db.commit()
    db.refresh(policy)
    return policy


@router.post("/ai/query", response_model=AIDraftOut, status_code=201)
def query_ai(
    payload: AIQueryRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    provider = db.scalar(
        select(AIProviderConfig).where(AIProviderConfig.enabled.is_(True))
    )
    policy = db.scalar(
        select(AIPolicy)
        .where(AIPolicy.active.is_(True))
        .order_by(AIPolicy.created_at)
    )
    if not policy or payload.capability.value not in policy.capabilities:
        raise ProblemException(403, "AI_CAPABILITY_DENIED", "AI 能力未授权", "请由管理员调整只读权限策略。")
    sources, excerpts = collect_sources(
        db,
        user,
        policy,
        payload.instruction,
        payload.task_ids,
        payload.file_ids,
        allow_sensitive_party_work=(
            user.role.value == "admin" and payload.confirm_sensitive
        ),
    )
    if not sources:
        raise ProblemException(422, "AI_SOURCE_EMPTY", "没有可用资料", "请先选择已授权事项或已索引文件。")
    private = bool(provider and is_private_endpoint(provider.base_url) and provider.trusted_intranet)
    if provider and not private and not payload.confirm_external:
        raise ProblemException(
            409,
            "AI_EXTERNAL_CONFIRM_REQUIRED",
            "需要确认资料外发",
            "当前接口不属于已信任内网。确认后只发送下列最小资料片段。",
            extra={"sources": sources, "source_count": len(sources)},
        )
    invocation = AIInvocation(
        user_id=user.id,
        provider_id=provider.id if provider else None,
        capability=payload.capability,
        source_count=len(sources),
        source_ids=[item["id"] for item in sources],
        status="running",
    )
    db.add(invocation)
    db.commit()
    try:
        if provider:
            content = call_compatible_model(
                provider,
                payload.capability,
                payload.instruction,
                excerpts,
            )
        else:
            content = complete_locally(db, payload.instruction, excerpts)
    except ProblemException as exc:
        invocation = db.get(AIInvocation, invocation.id)
        invocation.status = "failed"
        invocation.error_code = exc.code
        invocation.completed_at = utcnow()
        db.commit()
        raise
    draft = AIDraft(
        user_id=user.id,
        capability=payload.capability,
        title=f"AI 草稿 · {payload.capability.value}",
        content=content,
        sources=sources,
    )
    db.add(draft)
    invocation = db.get(AIInvocation, invocation.id)
    invocation.status = "completed"
    invocation.completed_at = utcnow()
    db.flush()
    write_audit(
        db,
        user,
        "ai.draft_create",
        "ai_draft",
        draft.id,
        {
            "capability": payload.capability.value,
            "source_count": len(sources),
            "provider_id": provider.id if provider else None,
            "generator": "external_llm" if provider else "local_llm",
        },
        client_ip(request),
    )
    emit_event(db, "ai.draft_created", draft.id, {"user_id": user.id})
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/ai/drafts", response_model=typing.List[AIDraftOut])
def list_ai_drafts(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIDraft]:
    return list(
        db.scalars(
            select(AIDraft)
            .where(AIDraft.user_id == user.id)
            .order_by(AIDraft.created_at.desc())
            .limit(100)
        ).all()
    )


@router.post("/ai/drafts/{draft_id}/discard", response_model=AIDraftOut)
def discard_ai_draft(
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    draft = db.get(AIDraft, draft_id)
    if not draft or draft.user_id != user.id:
        raise ProblemException(404, "AI_DRAFT_NOT_FOUND", "AI 草稿不存在", "未找到该草稿。")
    if draft.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 草稿已更新", "请刷新后重试。")
    draft.status = "discarded"
    draft.version += 1
    write_audit(db, user, "ai.draft_discard", "ai_draft", draft.id, {}, client_ip(request))
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/ai/approvals", response_model=typing.List[AIDraftOut])
def list_ai_approvals(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[AIDraft]:
    statement = select(AIDraft).where(AIDraft.status == "draft")
    if user.role.value != "admin":
        statement = statement.where(AIDraft.user_id == user.id)
    return list(db.scalars(statement.order_by(AIDraft.created_at.desc())).all())


@router.post("/ai/drafts/{draft_id}/approve", response_model=AIDraftOut)
def approve_ai_draft(
    draft_id: str,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> AIDraft:
    draft = db.get(AIDraft, draft_id)
    if not draft or (draft.user_id != user.id and user.role.value != "admin"):
        raise ProblemException(404, "AI_DRAFT_NOT_FOUND", "AI 草稿不存在", "未找到可审批草稿。")
    if draft.version != parse_version(if_match):
        raise ProblemException(409, "VERSION_CONFLICT", "AI 草稿状态已变化", "请刷新后重试。")
    if draft.status != "draft":
        raise ProblemException(409, "AI_DRAFT_ALREADY_HANDLED", "AI 草稿已处理", "该草稿不能重复审批。")
    draft.status = "approved"
    draft.version += 1
    write_audit(db, user, "ai.draft_approve", "ai_draft", draft.id, {}, client_ip(request))
    db.commit()
    db.refresh(draft)
    return draft
