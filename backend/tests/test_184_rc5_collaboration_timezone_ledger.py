"""rc.5 协同入网、北京时间与年度候选的端到端回归。"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app import client_agent
from app.config import get_settings
from app.enums import RecurrenceKind, TaskType, UserRole
from app.ledger_imports import derive_archive_year_candidates
from app.models import RecurrenceRule, Task, TaskTemplate, User
from app.schemas import TaskCreate


def _xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "2026年人事调动"
    sheet.append(["序号", "文号", "内容", "涉及人员", "出文时间"])
    sheet.append([1, "广组干〔2026〕12号", "关于张三同志岗位调动的通知", "张三", "2026-08-20"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_naive_user_datetime_is_beijing_and_explicit_offsets_remain_absolute() -> None:
    naive = TaskCreate(
        title="北京时间回归",
        owner_id="owner",
        formal_due_at="2026-08-28 18:00:00",
    )
    assert naive.formal_due_at == datetime(2026, 8, 28, 10, tzinfo=UTC)

    explicit = TaskCreate(
        title="显式时区回归",
        owner_id="owner",
        formal_due_at="2026-08-28T18:00:00+08:00",
        internal_due_at=datetime(2026, 8, 28, 18, tzinfo=timezone(timedelta(hours=8))),
    )
    assert explicit.formal_due_at == datetime(2026, 8, 28, 10, tzinfo=UTC)
    assert explicit.internal_due_at == datetime(2026, 8, 28, 10, tzinfo=UTC)

    # 数据库返回的无时区 datetime 按既有内部 UTC 契约保留；否则响应模型
    # 会把已经正确的 10:00 再减 8 小时。
    internal_utc = datetime(2026, 8, 28, 10)
    trusted = TaskCreate(title="内部 UTC 回归", owner_id="owner", formal_due_at=internal_utc)
    assert trusted.formal_due_at == internal_utc

    try:
        TaskCreate(title="非法时间", owner_id="owner", formal_due_at="不是日期")
    except ValidationError:
        pass
    else:  # pragma: no cover - Pydantic 必须拒绝非法日期，防止静默改期。
        raise AssertionError("非法日期未被拒绝")


def test_0025_moves_user_wall_time_but_never_moves_scheduler_time(tmp_path: Path) -> None:
    database = tmp_path / "timezone-0024-to-0025.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0024")
    wall_time = datetime(2026, 8, 28, 18)
    scheduler_utc = datetime(2026, 8, 28, 10)
    with Session(engine) as db:
        user = User(
            id="timezone-admin",
            username="timezone-admin",
            display_name="北京时间迁移管理员",
            password_hash="test-only",
            role=UserRole.ADMIN,
            active=True,
        )
        template = TaskTemplate(
            id="timezone-template",
            name="北京时间迁移模板",
            task_type=TaskType.QUICK,
            created_by=user.id,
        )
        task = Task(
            id="timezone-task",
            title="旧界面墙上时间",
            owner_id=user.id,
            created_by=user.id,
            updated_by=user.id,
            formal_due_at=wall_time,
        )
        rule = RecurrenceRule(
            id="timezone-rule",
            name="调度器 UTC 不得迁移",
            template_id=template.id,
            owner_id=user.id,
            kind=RecurrenceKind.MONTHLY,
            next_run_at=scheduler_utc,
        )
        db.add_all([user, template, task, rule])
        db.commit()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        task_value = connection.execute(
            text("SELECT formal_due_at FROM tasks WHERE id='timezone-task'")
        ).scalar_one()
        rule_value = connection.execute(
            text("SELECT next_run_at FROM recurrence_rules WHERE id='timezone-rule'")
        ).scalar_one()
        audit_count = connection.execute(
            text("SELECT count(*) FROM timezone_migration_audits WHERE entity_type='tasks' AND entity_id='timezone-task' AND field_name='formal_due_at'")
        ).scalar_one()
    assert str(task_value).startswith("2026-08-28 10:00:00")
    assert str(rule_value).startswith("2026-08-28 10:00:00")
    assert audit_count == 1
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0024")
        restored_task = connection.execute(
            text("SELECT formal_due_at FROM tasks WHERE id='timezone-task'")
        ).scalar_one()
        restored_rule = connection.execute(
            text("SELECT next_run_at FROM recurrence_rules WHERE id='timezone-rule'")
        ).scalar_one()
    assert str(restored_task).startswith("2026-08-28 18:00:00")
    assert str(restored_rule).startswith("2026-08-28 10:00:00")


def test_archive_year_candidates_record_sources_without_treating_them_as_fact() -> None:
    rows = [
        ["序号", "文号", "内容", "涉及人员", "出文时间"],
        [1, "广组干〔2025〕9号", "调动通知", "李四", "2026-01-02"],
    ]
    candidates = derive_archive_year_candidates(
        "2026年人事调动文件档案目录.xlsx",
        "2026年目录",
        rows,
        1,
    )
    by_year = {item["year"]: item for item in candidates}
    assert by_year[2026]["confirmed"] is False
    assert {"文件名", "工作表名", "列“出文时间”"}.issubset(by_year[2026]["sources"])
    assert "列“文号”" in by_year[2025]["sources"]
    assert derive_archive_year_candidates("无年度.xlsx", "目录", [], 1) == []


def test_client_agent_sends_enrollment_code_only_in_the_dedicated_header(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return json.dumps({"device_id": "device-1"}).encode("utf-8")

    def fake_urlopen(request, _timeout):
        captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    monkeypatch.setattr(client_agent, "_urlopen", fake_urlopen)
    result = client_agent._json_request(
        "https://192.168.1.20:18765/api/v1/devices/enroll",
        enrollment_code="一次性入网码",
        payload={"code": "一次性入网码"},
        method="POST",
    )
    assert result == {"device_id": "device-1"}
    assert captured["headers"]["x-partyops-enrollment-code"] == "一次性入网码"
    assert "x-partyops-device-token" not in captured["headers"]
    assert captured["body"] == {"code": "一次性入网码"}


def test_production_enrollment_uses_one_time_header_without_opening_other_writes(
    client: TestClient,
    admin: dict,
    monkeypatch,
) -> None:
    enrollment = client.post(
        "/api/v1/admin/devices/enrollments",
        json={"name": "UOS 重绑终端"},
    )
    assert enrollment.status_code == 201, enrollment.text
    code = enrollment.json()["code"]
    payload = {
        "code": code,
        "name": "UOS 重绑终端",
        "architecture": "arm64",
        "platform": "uos",
        "kernel": "4.19.0-arm64-desktop",
        "agent_version": "1.4.5-rc.5",
        "local_username": "staff",
    }
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "advertise_host", "192.168.1.20")

    missing = client.post(
        "http://127.0.0.1/api/v1/devices/enroll",
        headers={"Origin": "http://127.0.0.1"},
        json=payload,
    )
    assert missing.status_code == 403
    assert missing.json()["code"] == "ENROLLMENT_TOKEN_REQUIRED"

    invalid = client.post(
        "http://127.0.0.1/api/v1/devices/enroll",
        headers={"Origin": "https://malicious.invalid", "X-PartyOps-Enrollment-Code": "bad"},
        json=payload,
    )
    assert invalid.status_code == 403
    assert invalid.json()["code"] == "ENROLLMENT_TOKEN_INVALID"

    replacement = ("A" if code[0] != "A" else "B") + code[1:]
    mismatch = client.post(
        "http://127.0.0.1/api/v1/devices/enroll",
        headers={"Origin": "https://malicious.invalid", "X-PartyOps-Enrollment-Code": replacement},
        json=payload,
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["code"] == "ENROLLMENT_TOKEN_MISMATCH"

    enrolled = client.post(
        "http://127.0.0.1/api/v1/devices/enroll",
        headers={"Origin": "https://malicious.invalid", "X-PartyOps-Enrollment-Code": code},
        json=payload,
    )
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["device_id"]

    # 同一个安全头不能替其它写接口绕过 Origin 门禁。
    unrelated = client.post(
        "http://127.0.0.1/api/v1/tasks",
        headers={"Origin": "https://malicious.invalid", "X-PartyOps-Enrollment-Code": code},
        json={"title": "不得创建", "owner_id": admin["id"]},
    )
    assert unrelated.status_code == 403
    assert unrelated.json()["code"] == "ORIGIN_DENIED"


def test_archive_import_requires_explicit_confirmation_of_derived_year(
    client: TestClient,
    admin: dict,
) -> None:
    category = next(
        item
        for item in client.get("/api/v1/archives/categories").json()
        if item["name"] == "人事调动文件"
    )
    inspected = client.post(
        "/api/v1/ledger-imports/inspect",
        data={"target_type": "archive", "target_id": category["id"]},
        files={
            "file": (
                "2026年人事调动文件档案目录.xlsx",
                _xlsx(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert inspected.status_code == 201, inspected.text
    job = inspected.json()
    assert job["derived_candidates"][0]["year"] == 2026
    profiled = client.patch(
        f"/api/v1/ledger-imports/{job['id']}/profile",
        json={
            "sheet_name": job["sheet_name"],
            "header_row": job["header_row"],
            "version": job["version"],
        },
    )
    assert profiled.status_code == 200, profiled.text
    job = profiled.json()
    assert job["derived_candidates"][0]["year"] == 2026
    mappings = []
    target_by_header = {
        "文号": "document_no",
        "内容": "title",
        "涉及人员": "involved_persons",
        "出文时间": "document_date",
    }
    for column in job["profile"]["selected"]["columns"]:
        header = column["header"]
        if header == "序号":
            mappings.append({"source_column": header, "action": "ignore"})
        else:
            mappings.append(
                {
                    "source_column": header,
                    "action": "map",
                    "target_field": target_by_header[header],
                    "confirmed": True,
                }
            )
    mapped = client.patch(
        f"/api/v1/ledger-imports/{job['id']}/mapping",
        json={
            "sheet_name": job["sheet_name"],
            "header_row": job["header_row"],
            "mappings": mappings,
            "version": job["version"],
        },
    )
    assert mapped.status_code == 200, mapped.text
    current = mapped.json()

    missing = client.post(
        f"/api/v1/ledger-imports/{job['id']}/validate",
        json={"version": current["version"], "row_actions": {}},
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "LEDGER_DERIVED_YEAR_CONFIRM_REQUIRED"
    wrong = client.post(
        f"/api/v1/ledger-imports/{job['id']}/validate",
        json={"version": current["version"], "row_actions": {}, "derived_year": 2024},
    )
    assert wrong.status_code == 422

    validated = client.post(
        f"/api/v1/ledger-imports/{job['id']}/validate",
        json={"version": current["version"], "row_actions": {}, "derived_year": 2026},
    )
    assert validated.status_code == 200, validated.text
    current = validated.json()
    assert current["manual_edits"]["archive_year"] == 2026

    unconfirmed = client.post(
        f"/api/v1/ledger-imports/{job['id']}/commit",
        json={
            "version": current["version"],
            "confirm_shared_storage": True,
            "derived_year": 2026,
        },
    )
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "LEDGER_DERIVED_YEAR_CONFIRM_REQUIRED"
    inconsistent = client.post(
        f"/api/v1/ledger-imports/{job['id']}/commit",
        json={
            "version": current["version"],
            "confirm_shared_storage": True,
            "confirm_derived_candidates": True,
            "derived_year": 2025,
        },
    )
    assert inconsistent.status_code == 422

    committed = client.post(
        f"/api/v1/ledger-imports/{job['id']}/commit",
        json={
            "version": current["version"],
            "confirm_shared_storage": True,
            "confirm_derived_candidates": True,
            "derived_year": 2026,
        },
    )
    assert committed.status_code == 200, committed.text
    records = client.get(
        "/api/v1/archives/records",
        params={"category_id": category["id"], "archive_year": 2026},
    )
    assert any(item["document_no"] == "广组干〔2026〕12号" for item in records.json())
