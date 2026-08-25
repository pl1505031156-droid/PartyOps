"""面向接近全覆盖目标的真实安全与业务边界回归。"""

from __future__ import annotations

import socket
import ssl
import urllib.error
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import (
    ai_service,
    needle_intent,
    pki,
    recurrence,
    windows_host_status,
    workspace,
)
from app.enums import FileIndexStatus, ModelPackStatus, Sensitivity
from app.problems import ProblemException
from app.routers import updates


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class _CalendarDb:
    def __init__(self, entries):
        self.entries = entries

    def scalars(self, _statement):
        return _Rows(self.entries)


def _code(code: str, call) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == code


def test_ai_secret_dns_rebinding_and_invalid_port_are_classified(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    monkeypatch.setattr(
        ai_service,
        "get_settings",
        lambda: SimpleNamespace(secrets_dir=secrets_dir),
    )
    monkeypatch.setattr(
        ai_service.os,
        "chmod",
        lambda *_args: (_ for _ in ()).throw(OSError("acl unavailable")),
    )
    encrypted = ai_service.encrypt_api_key("local-secret")
    assert ai_service.decrypt_api_key(encrypted) == "local-secret"
    _code("AI_KEY_UNAVAILABLE", lambda: ai_service.decrypt_api_key("invalid-token"))

    _code(
        "AI_URL_INVALID",
        lambda: ai_service.validate_provider_url(
            "https://model.example:invalid", False, resolve=True
        ),
    )
    monkeypatch.setattr(
        ai_service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("dns down")),
    )
    _code(
        "AI_PROVIDER_UNREACHABLE",
        lambda: ai_service.validate_provider_url(
            "https://model.example", False, resolve=True
        ),
    )
    monkeypatch.setattr(
        ai_service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("8.8.8.8", 443)),
            (None, None, None, None, ("10.1.2.3", 443)),
        ],
    )
    _code(
        "AI_ENDPOINT_FORBIDDEN",
        lambda: ai_service.validate_provider_url(
            "https://model.example", True, resolve=True
        ),
    )


def test_ai_sensitive_meeting_and_automatic_source_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(
        id="task-1",
        title="党委会材料",
        description="准备审议材料",
        experience_notes="",
        category="综合工作",
        sensitivity=Sensitivity.NORMAL,
    )
    policy = SimpleNamespace(allowed_task_categories=[], allowed_root_ids=["root-1"])
    db = SimpleNamespace(
        scalar=lambda _statement: SimpleNamespace(meeting_type="party_member_meeting"),
        get=lambda *_args: None,
    )
    monkeypatch.setattr(ai_service, "can_view_task", lambda *_args: True)
    _code(
        "AI_PARTY_WORK_DENIED",
        lambda: ai_service._task_source(db, task, SimpleNamespace(id="user"), policy),
    )

    monkeypatch.setattr(ai_service, "visible_tasks", lambda *_args: [task])
    monkeypatch.setattr(
        ai_service,
        "_task_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProblemException(403, "DENIED", "拒绝", "拒绝")
        ),
    )
    files = [
        SimpleNamespace(root_id="other"),
        SimpleNamespace(root_id="root-1"),
    ]
    monkeypatch.setattr(ai_service, "search_workspace_files", lambda *_args, **_kwargs: files)
    monkeypatch.setattr(
        ai_service,
        "_file_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ProblemException(403, "DENIED", "拒绝", "拒绝")
        ),
    )
    assert ai_service.collect_sources(
        db,
        SimpleNamespace(id="user"),
        policy,
        "",
        [],
        [],
    ) == ([], [])


@pytest.mark.parametrize(
    ("pack", "effect", "response", "flag"),
    [
        (
            SimpleNamespace(
                id="corrupt",
                status=ModelPackStatus.CORRUPT,
                manifest={"components": {"intent_router": {}}},
            ),
            None,
            None,
            "NEEDLE_PACK_INVALID",
        ),
        (
            SimpleNamespace(
                id="runtime",
                status=ModelPackStatus.ACTIVE,
                manifest={"components": {"intent_router": {}}},
            ),
            RuntimeError("native failure"),
            None,
            "NEEDLE_RUNTIME_ERROR",
        ),
        (
            SimpleNamespace(
                id="threshold",
                status=ModelPackStatus.ACTIVE,
                manifest={
                    "components": {
                        "intent_router": {"confidence_threshold": "invalid"}
                    }
                },
            ),
            None,
            {"confidence": 0.9, "function_calls": []},
            "NEEDLE_NO_UNIQUE_INTENT",
        ),
        (
            SimpleNamespace(
                id="schema",
                status=ModelPackStatus.ACTIVE,
                manifest={"components": {"intent_router": {}}},
            ),
            None,
            {
                "confidence": 0.95,
                "function_calls": [{"name": "unknown", "arguments": {}}],
            },
            "NEEDLE_SCHEMA_REJECTED",
        ),
    ],
)
def test_needle_safe_fallback_matrix(
    monkeypatch: pytest.MonkeyPatch, pack, effect, response, flag
) -> None:
    monkeypatch.setattr(needle_intent, "active_model_pack", lambda *_args: pack)
    monkeypatch.setattr(needle_intent, "verify_installed_pack", lambda _pack: True)

    def complete(*_args, **_kwargs):
        if effect is not None:
            raise effect
        return response

    monkeypatch.setattr(needle_intent.needle_intent_runtime, "complete", complete)
    value = needle_intent.preview_intent_with_needle(
        object(), "搜索会议", today=date(2026, 8, 25)
    )
    assert value["engine"] == "rules" and flag in value["flags"]


def test_recurrence_month_day_and_exhausted_workday_calendars() -> None:
    base = datetime(2026, 1, 31, 9, tzinfo=UTC)
    rule = SimpleNamespace(
        kind=recurrence.RecurrenceKind.MONTHLY,
        custom_days=None,
        schedule_config={"mode": "day_of_month", "day": 31},
    )
    assert recurrence.next_occurrence(rule, base) == datetime(
        2026, 2, 28, 9, tzinfo=UTC
    )

    # 显式把连续日期全部标记为非工作日，验证有界循环会拒绝坏日历，
    # 而不是无限运行或悄悄生成错误日期。
    entries = []
    for offset in range(-370, 32):
        value = base + timedelta(days=offset)
        local = value.astimezone(recurrence.timezone(timedelta(hours=8))).date()
        entries.append(SimpleNamespace(date_key=local.isoformat(), is_workday=False))
    db = _CalendarDb(entries)
    last_workday = SimpleNamespace(
        schedule_config={"mode": "last_workday", "workday_policy": "unchanged"}
    )
    _code(
        "WORK_CALENDAR_INVALID",
        lambda: recurrence.scheduled_occurrence(db, last_workday, base),
    )
    _code(
        "WORK_CALENDAR_INVALID",
        lambda: recurrence.adjusted_internal_due(db, "owner", base, 0),
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            urllib.error.HTTPError(
                "https://partyops.cn/releases/update-v3.json",
                503,
                "service unavailable",
                None,
                None,
            ),
            "UPDATE_CATALOG_HTTP_ERROR",
        ),
        (urllib.error.URLError(socket.gaierror("dns")), "UPDATE_CATALOG_DNS_FAILED"),
        (urllib.error.URLError(ssl.SSLError("tls")), "UPDATE_CATALOG_TLS_FAILED"),
        (urllib.error.URLError(TimeoutError("timed out")), "UPDATE_CATALOG_TIMEOUT"),
        (urllib.error.URLError("proxy tunnel failed"), "UPDATE_CATALOG_PROXY_FAILED"),
        (OSError("network unreachable"), "UPDATE_CATALOG_NETWORK_FAILED"),
    ],
)
def test_update_catalog_network_errors_are_precisely_classified(error, code) -> None:
    assert updates._catalog_network_problem(error).code == code


def test_update_v4_artifact_contract_accepts_one_target_and_rejects_confusion() -> None:
    filename = "PartyOps_1.4.5-rc.3_windows_amd64.exe"
    manifest = {
        "package_role": "platform-update",
        "target_platform": "windows",
        "target_architecture": "amd64",
        "platform_artifacts": {"windows": {"amd64": filename}},
    }
    updates._validate_v4_platform_artifacts(manifest, {filename: {"size": 1}})
    confused = dict(manifest)
    confused["platform_artifacts"] = {
        "windows": {"amd64": filename},
        "linux-deb": {"amd64": "unexpected.deb"},
    }
    _code(
        "UPDATE_PLATFORM_TARGET_INVALID",
        lambda: updates._validate_v4_platform_artifacts(
            confused, {filename: {"size": 1}}
        ),
    )


def test_private_write_fallback_and_loopback_invalid_json(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private" / "secret.pem"
    monkeypatch.setattr(pki.os, "name", "posix")
    original_chmod = Path.chmod

    def denied_chmod(self, mode, **kwargs):
        if self.suffix == ".tmp":
            raise OSError("chmod unavailable")
        return original_chmod(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", denied_chmod)
    with pytest.raises(OSError, match="chmod unavailable"):
        pki._write_private(target, b"secret")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr(
        windows_host_status.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    healthy, detail = windows_host_status.probe_loopback_health(18765, tls=False)
    assert healthy is False and detail


def test_workspace_scan_recovers_per_file_metadata_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "blocked-dir").mkdir()
    (tmp_path / "recover.txt").write_text("recover", encoding="utf-8")
    (tmp_path / "broken.txt").write_text("broken", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")

    missing = SimpleNamespace(
        relative_path="gone.txt",
        status=FileIndexStatus.INDEXED,
        version=1,
        in_scope=True,
        extracted_text="old",
        ocr_text="old",
    )
    outside = SimpleNamespace(
        id="outside",
        relative_path="outside.txt",
        status=FileIndexStatus.INDEXED,
        version=1,
        in_scope=False,
        extracted_text="must-clear",
        ocr_text="must-clear",
    )
    persisted = SimpleNamespace(
        id="persisted",
        relative_path="recover.txt",
        in_scope=True,
        extracted_text="",
        ocr_text="",
    )

    class Nested:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Db:
        def __init__(self):
            # 文件名按字典序处理：broken 首次失败后数据库无记录，recover
            # 首次失败后能重新读取已持久化记录再降级到轻量元数据。
            self.scalar_values = iter([None, persisted])
            self.commits = 0

        def scalars(self, _statement):
            return _Rows([missing, outside])

        def scalar(self, _statement):
            return next(self.scalar_values)

        def begin_nested(self):
            return Nested()

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("单文件故障不应回滚整批扫描")

    root = SimpleNamespace(
        id="root-1",
        absolute_path=str(tmp_path),
        selection_mode="selected",
        included_paths=["."],
        semantic_content_enabled=False,
        scan_status="idle",
        error_message="",
        last_scan_at=None,
        file_count=0,
        directory_count=0,
    )
    db = Db()
    monkeypatch.setattr(workspace, "validate_root_path", lambda _path: tmp_path)
    monkeypatch.setattr(
        workspace,
        "path_scope_state",
        lambda relative, **_kwargs: relative != "outside.txt",
    )
    attempts: dict[str, int] = {}

    def upsert(
        _db,
        _root,
        _existing,
        _parent_id,
        relative,
        _path,
        is_directory,
        _scanned_at,
        **_kwargs,
    ):
        attempts[relative] = attempts.get(relative, 0) + 1
        if is_directory:
            raise OSError("directory metadata denied")
        if relative == "recover.txt" and attempts[relative] == 1:
            raise RuntimeError("file moved during metadata read")
        if relative == "broken.txt":
            raise RuntimeError("file remains locked")
        if relative == "outside.txt":
            return outside, False
        item = SimpleNamespace(
            id=f"item-{relative}",
            content_status=None,
            content_error_code="old",
        )
        return item, True

    monkeypatch.setattr(workspace, "_upsert_node", upsert)
    result = workspace._scan_root_locked(db, root)
    assert result.files == 1
    assert result.missing == 1
    assert result.skipped_directories == 1
    assert len(result.errors) == 2
    assert root.scan_status == "completed_with_errors"
    assert missing.status == FileIndexStatus.MISSING
    assert outside.extracted_text == "" and outside.ocr_text == ""
