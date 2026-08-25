"""OpenAI 兼容模型接入、机器密钥和最小范围资料组装。"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .enums import AiCapability, FileIndexStatus, Sensitivity
from .models import (
    AIPolicy,
    AIProviderConfig,
    BusinessMeeting,
    Task,
    User,
    WorkspaceFile,
    WorkspaceRoot,
)
from .problems import ProblemException
from .task_service import can_view_task, visible_tasks
from .workspace import search_workspace_files


def _machine_key_path() -> Path:
    return get_settings().secrets_dir / "ai-fernet.key"


def _machine_fernet() -> Fernet:
    path = _machine_key_path()
    if not path.exists():
        temporary = path.with_suffix(".new")
        temporary.write_bytes(Fernet.generate_key())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    return Fernet(path.read_bytes().strip())


def encrypt_api_key(value: str) -> str:
    return _machine_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_api_key(value: str) -> str:
    if not value:
        return ""
    try:
        return _machine_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ProblemException(
            409,
            "AI_KEY_UNAVAILABLE",
            "AI 密钥无法解密",
            "主机密钥可能已更换，请重新输入 AI 密钥。",
        ) from exc


def is_private_endpoint(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    if host == "localhost" or host.endswith((".local", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def validate_provider_url(
    base_url: str,
    trusted_intranet: bool,
    *,
    resolve: bool,
) -> bool:
    """验证模型地址并在每次出站前重新解析，降低 SSRF 与 DNS 重绑定风险。"""

    try:
        parsed = urlparse(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ProblemException(422, "AI_URL_INVALID", "接口地址无效", "接口端口格式错误。") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProblemException(
            422,
            "AI_URL_INVALID",
            "接口地址无效",
            "仅允许不含账号、查询参数和片段的 http(s) 模型服务地址。",
        )
    host = parsed.hostname.lower()
    scopes: set[str] = set()

    def classify(value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return "unknown"
        if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            return "forbidden"
        if address.is_private or address.is_loopback:
            return "private"
        return "public" if address.is_global else "forbidden"

    literal_scope = classify(host)
    if literal_scope != "unknown":
        scopes.add(literal_scope)
    elif host == "localhost" or host.endswith((".local", ".lan")):
        scopes.add("private")
    elif resolve:
        try:
            for result in socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM):
                scopes.add(classify(str(result[4][0])))
        except OSError as exc:
            raise ProblemException(
                502,
                "AI_PROVIDER_UNREACHABLE",
                "AI 服务地址无法解析",
                "请检查模型服务域名和单位网络。",
            ) from exc
    else:
        scopes.add("public")
    if "forbidden" in scopes or ("private" in scopes and "public" in scopes):
        raise ProblemException(
            422,
            "AI_ENDPOINT_FORBIDDEN",
            "模型服务地址被拒绝",
            "禁止访问链路本地、保留地址或同时解析到内外网的地址。",
        )
    private = "private" in scopes
    if private and not trusted_intranet:
        raise ProblemException(
            422,
            "AI_ENDPOINT_PRIVATE_DENIED",
            "内网模型服务未受信",
            "访问内网或本机地址前必须由管理员显式启用“受信内网”。",
        )
    if not private and parsed.scheme != "https":
        raise ProblemException(
            422,
            "AI_ENDPOINT_TLS_REQUIRED",
            "外部模型服务必须使用 HTTPS",
            "请改用经过 TLS 保护的模型接口。",
        )
    return private


def provider_output(provider: AIProviderConfig | None) -> dict[str, object]:
    if not provider:
        return {
            "id": None,
            "name": "DeepSeek 模型服务",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "has_api_key": False,
            "enabled": False,
            "trusted_intranet": False,
            "timeout_seconds": 60,
            "version": 0,
            "last_test_at": None,
            "last_status": "not_configured",
            "last_error": "",
        }
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "model": provider.model,
        "has_api_key": bool(provider.api_key_encrypted),
        "enabled": provider.enabled,
        "trusted_intranet": provider.trusted_intranet,
        "timeout_seconds": provider.timeout_seconds,
        "version": provider.version,
        "last_test_at": provider.last_test_at,
        "last_status": provider.last_status,
        "last_error": provider.last_error,
    }


def endpoint_url(base_url: str, suffix: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/v1"):
        return f"{value}/{suffix.lstrip('/')}"
    return f"{value}/v1/{suffix.lstrip('/')}"


def test_provider(provider: AIProviderConfig) -> None:
    validate_provider_url(provider.base_url, provider.trusted_intranet, resolve=True)
    key = decrypt_api_key(provider.api_key_encrypted)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        with httpx.Client(timeout=provider.timeout_seconds, follow_redirects=False) as client:
            response = client.get(endpoint_url(provider.base_url, "models"), headers=headers)
            if response.status_code >= 400:
                raise ProblemException(
                    502,
                    "AI_PROVIDER_REJECTED",
                    "AI 服务连接失败",
                    f"模型服务返回 HTTP {response.status_code}，请检查地址、密钥和模型权限。",
                )
    except httpx.HTTPError as exc:
        raise ProblemException(
            502,
            "AI_PROVIDER_UNREACHABLE",
            "AI 服务不可达",
            "请检查模型服务地址、单位网络和代理设置。",
        ) from exc


def _citation_excerpt(value: str, limit: int = 240) -> str:
    """生成可供经办人核对的最小单行引用，不保存整篇正文。"""

    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:limit] + ("…" if len(normalized) > limit else "")


def _task_source(
    db: Session,
    task: Task,
    user: User,
    policy: AIPolicy,
    *,
    allow_sensitive_party_work: bool = False,
) -> tuple[dict[str, str], str]:
    if not can_view_task(db, task, user):
        raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到可访问事项。")
    if task.sensitivity == Sensitivity.RESTRICTED:
        raise ProblemException(
            403,
            "AI_RESTRICTED_TASK_DENIED",
            "敏感事项禁止进入 AI",
            "请移除敏感事项后重试。",
        )
    if (
        policy.allowed_task_categories
        and task.category not in policy.allowed_task_categories
    ):
        raise ProblemException(
            403,
            "AI_TASK_SCOPE_DENIED",
            "事项不在 AI 授权范围",
            "请由管理员调整允许的工作类别。",
        )
    meeting = db.scalar(
        select(BusinessMeeting).where(BusinessMeeting.task_id == task.id)
    )
    sensitive_party_work = bool(
        meeting
        and meeting.meeting_type
        in {
            "party_member_meeting",
            "branch_members",
            "party_group",
            "party_class",
            "study_group",
        }
    ) or task.category in {"发展党员", "党员发展", "党务落实"}
    if sensitive_party_work and not allow_sensitive_party_work:
        raise ProblemException(
            403,
            "AI_PARTY_WORK_DENIED",
            "敏感党务资料默认不进入 AI",
            "仅管理员可在核对最小片段后逐次明确授权。",
        )
    text_value = "\n".join(
        part
        for part in (task.title, task.description, task.experience_notes)
        if part.strip()
    )[:12_000]
    return (
        {
            "type": "task",
            "id": task.id,
            "name": task.title,
            "citation": _citation_excerpt(text_value),
        },
        f"[事项：{task.title}]\n{text_value}",
    )


def _file_source(
    db: Session, item: WorkspaceFile, policy: AIPolicy
) -> tuple[dict[str, str], str]:
    root = db.get(WorkspaceRoot, item.root_id)
    if (
        not root
        or not root.enabled
        or item.status == FileIndexStatus.MISSING
        or item.root_id not in policy.allowed_root_ids
    ):
        raise ProblemException(
            403,
            "AI_FILE_SCOPE_DENIED",
            "文件不在 AI 授权范围",
            "请由管理员显式授权该文件根目录。",
        )
    if policy.allowed_file_types and item.extension not in policy.allowed_file_types:
        raise ProblemException(
            403,
            "AI_FILE_TYPE_DENIED",
            "文件类型不允许进入 AI",
            "请由管理员调整允许的文件类型。",
        )
    text_value = (item.extracted_text or item.ocr_text).strip()
    if not text_value:
        raise ProblemException(
            422,
            "AI_FILE_TEXT_UNAVAILABLE",
            "文件没有可用正文",
            "请先完成文件扫描或 OCR。",
        )
    return (
        {
            "type": "file",
            "id": item.id,
            "name": item.name,
            "root": root.name,
            "citation": _citation_excerpt(text_value),
        },
        f"[文件：{item.name}]\n{text_value[:12_000]}",
    )


def collect_sources(
    db: Session,
    user: User,
    policy: AIPolicy,
    instruction: str,
    task_ids: list[str],
    file_ids: list[str],
    *,
    allow_sensitive_party_work: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    sources: list[dict[str, str]] = []
    excerpts: list[str] = []
    for task_id in list(dict.fromkeys(task_ids)):
        task = db.get(Task, task_id)
        if not task:
            raise ProblemException(404, "TASK_NOT_FOUND", "事项不存在", "未找到所选事项。")
        source, excerpt = _task_source(
            db,
            task,
            user,
            policy,
            allow_sensitive_party_work=allow_sensitive_party_work,
        )
        sources.append(source)
        excerpts.append(excerpt)
    for file_id in list(dict.fromkeys(file_ids)):
        item = db.get(WorkspaceFile, file_id)
        if not item:
            raise ProblemException(404, "WORKSPACE_FILE_NOT_FOUND", "文件不存在", "未找到所选文件。")
        source, excerpt = _file_source(db, item, policy)
        sources.append(source)
        excerpts.append(excerpt)
    if not sources:
        lowered = instruction.strip().lower()
        for task in visible_tasks(db, user):
            if lowered and lowered not in (task.title + task.description + task.category).lower():
                continue
            try:
                source, excerpt = _task_source(
                    db,
                    task,
                    user,
                    policy,
                    allow_sensitive_party_work=allow_sensitive_party_work,
                )
            except ProblemException:
                continue
            sources.append(source)
            excerpts.append(excerpt)
            if len(sources) >= 5:
                break
        remaining = max(0, 8 - len(sources))
        if remaining and policy.allowed_root_ids:
            for item in search_workspace_files(db, instruction, limit=remaining * 2):
                if item.root_id not in policy.allowed_root_ids:
                    continue
                try:
                    source, excerpt = _file_source(db, item, policy)
                except ProblemException:
                    continue
                sources.append(source)
                excerpts.append(excerpt)
                if len(sources) >= 8:
                    break
    return sources, excerpts


def call_compatible_model(
    provider: AIProviderConfig,
    capability: AiCapability,
    instruction: str,
    excerpts: list[str],
) -> str:
    validate_provider_url(provider.base_url, provider.trusted_intranet, resolve=True)
    key = decrypt_api_key(provider.api_key_encrypted)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    payload = {
        "model": provider.model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是党建智办中的只读工作助手。只能依据提供片段生成草稿，"
                    "不得声称已修改任务、文件或报告；资料不足时明确说明。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"能力：{capability.value}\n要求：{instruction}\n\n"
                    + "\n\n".join(excerpts)
                )[:80_000],
            },
        ],
    }
    try:
        with httpx.Client(timeout=provider.timeout_seconds, follow_redirects=False) as client:
            response = client.post(
                endpoint_url(provider.base_url, "chat/completions"),
                headers=headers,
                json=payload,
            )
            if response.status_code >= 400:
                raise ProblemException(
                    502,
                    "AI_PROVIDER_REJECTED",
                    "AI 服务拒绝请求",
                    f"模型服务返回 HTTP {response.status_code}，未保存任何业务修改。",
                )
            data = response.json()
            content = data["choices"][0]["message"]["content"]
    except ProblemException:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProblemException(
            502,
            "AI_RESPONSE_INVALID",
            "AI 服务响应无效",
            "未获得兼容的模型响应，未保存任何业务修改。",
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ProblemException(502, "AI_RESPONSE_EMPTY", "AI 返回空内容", "请检查模型配置后重试。")
    return content.strip()[:100_000]
