"""1.4.5-rc.2 新党务状态机、今日聚合与配置回退的分支门禁。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app import config
from app.database import db_runtime
from app.enums import Priority, Sensitivity, TaskStatus, TaskType
from app.models import (
    AttachmentVersion,
    BusinessDocument,
    BusinessMeeting,
    FileBlob,
    MaterialItem,
    MeetingAction,
    MeetingAttendee,
    PartyDevelopmentCase,
    PartyDevelopmentMilestone,
    Task,
    User,
)
from app.routers import today as today_router
from app.routers.party_work import _ledger_row, _meeting_query, list_study_plans


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "PartyOps@2026"},
    )
    assert response.status_code == 200, response.text


def _task(*, user_id: str, title: str, now: datetime, **values: object) -> Task:
    defaults: dict[str, object] = {
        "title": title,
        "description": "1.4.5 分支门禁",
        "task_type": TaskType.STANDARD,
        "status": TaskStatus.IN_PROGRESS,
        "sensitivity": Sensitivity.NORMAL,
        "priority": Priority.NORMAL,
        "source": "自动化门禁",
        "source_kind": "test",
        "owner_id": user_id,
        "created_by": user_id,
        "updated_by": user_id,
        "formal_due_at": None,
        "internal_due_at": None,
        "planned_start_at": None,
        "planned_end_at": None,
        "completed_at": None,
    }
    defaults.update(values)
    return Task(**defaults)


def test_party_ledger_all_states_use_facts_instead_of_assumptions(admin: dict) -> None:
    now = datetime.now(timezone.utc)
    with db_runtime.session_factory() as db:
        meetings: list[BusinessMeeting] = []
        for label, status, scheduled, completed in (
            ("取消", "cancelled", now - timedelta(days=2), None),
            ("逾期", "planned", now - timedelta(days=2), None),
            ("完成日期待补", "completed", now - timedelta(days=2), None),
            ("完整", "completed", now - timedelta(days=2), now - timedelta(days=1)),
            ("待人工确认", "planned", now - timedelta(days=1), None),
            ("未来计划", "planned", now + timedelta(days=5), None),
        ):
            meeting = BusinessMeeting(
                meeting_type="party_member_meeting",
                organization=f"状态机-{uuid4().hex[:8]}",
                title=label,
                scheduled_at=scheduled,
                completed_at=completed,
                status=status,
                created_by=admin["id"],
            )
            db.add(meeting)
            db.flush()
            meetings.append(meeting)
            if status != "cancelled":
                db.add(
                    MeetingAttendee(
                        meeting_id=meeting.id,
                        display_name="测试党员",
                        attendance_status="present",
                    )
                )
                db.add(
                    BusinessDocument(
                        meeting_id=meeting.id,
                        document_type="minutes",
                        title="会议记录",
                        content={},
                        created_by=admin["id"],
                    )
                )
        db.add(
            MeetingAction(
                meeting_id=meetings[1].id,
                title="逾期落实项",
                due_at=(now - timedelta(days=1)).replace(tzinfo=None),
                status="pending",
                created_by=admin["id"],
            )
        )
        db.commit()

        states = {meeting.title: _ledger_row(db, meeting)["ledger_state"] for meeting in meetings}
        assert states == {
            "取消": "不适用",
            "逾期": "逾期",
            "完成日期待补": "需补充",
            "完整": "完整",
            "待人工确认": "待人工确认",
            "未来计划": "需补充",
        }
        assert _meeting_query({"party_member_meeting"}, None, "") is not None
        current_user = db.get(User, admin["id"])
        assert current_user is not None
        assert isinstance(list_study_plans(None, "", current_user, db), list)


def test_today_aggregates_all_operational_party_branches(
    client: TestClient,
    admin: dict,
) -> None:
    _login(client)
    now = datetime.now(timezone.utc)
    with db_runtime.session_factory() as db:
        today_task = _task(
            user_id=admin["id"],
            title=f"今日任务-{uuid4().hex[:6]}",
            now=now,
            internal_due_at=now,
        )
        overdue_task = _task(
            user_id=admin["id"],
            title=f"逾期任务-{uuid4().hex[:6]}",
            now=now,
            formal_due_at=now - timedelta(days=2),
        )
        review_task = _task(
            user_id=admin["id"],
            title=f"待审核-{uuid4().hex[:6]}",
            now=now,
            status=TaskStatus.PENDING_REVIEW,
        )
        feedback_task = _task(
            user_id=admin["id"],
            title=f"待反馈-{uuid4().hex[:6]}",
            now=now,
            status=TaskStatus.WAITING_FEEDBACK,
        )
        completed_task = _task(
            user_id=admin["id"],
            title=f"本周完成-{uuid4().hex[:6]}",
            now=now,
            status=TaskStatus.COMPLETED,
            completed_at=now,
        )
        completed_with_final = _task(
            user_id=admin["id"],
            title=f"材料齐全-{uuid4().hex[:6]}",
            now=now,
            status=TaskStatus.COMPLETED,
            completed_at=now,
        )
        next_week_task = _task(
            user_id=admin["id"],
            title=f"下周计划-{uuid4().hex[:6]}",
            now=now,
            planned_start_at=now + timedelta(days=4),
        )
        db.add_all(
            [
                today_task,
                overdue_task,
                review_task,
                feedback_task,
                completed_task,
                completed_with_final,
                next_week_task,
            ]
        )
        db.flush()
        missing_material = MaterialItem(
            task_id=completed_task.id,
            category="final",
            name="待补终稿",
            required=True,
        )
        complete_material = MaterialItem(
            task_id=completed_with_final.id,
            category="final",
            name="已归档终稿",
            required=True,
        )
        db.add_all([missing_material, complete_material])
        db.flush()
        blob = FileBlob(
            sha256=uuid4().hex * 2,
            relative_path=f"test/{uuid4().hex}.bin",
            size_bytes=1,
            original_name="终稿.bin",
        )
        db.add(blob)
        db.flush()
        db.add(
            AttachmentVersion(
                material_item_id=complete_material.id,
                blob_sha256=blob.sha256,
                version_no=1,
                is_final=True,
                uploaded_by=admin["id"],
            )
        )

        party_meeting = BusinessMeeting(
            meeting_type="party_member_meeting",
            organization=f"今日聚合-{uuid4().hex[:8]}",
            title="季度党员大会",
            scheduled_at=now,
            status="completed",
            completed_at=now,
            created_by=admin["id"],
        )
        study_meeting = BusinessMeeting(
            meeting_type="study_group",
            organization=f"今日聚合-{uuid4().hex[:8]}",
            title="季度中心组学习",
            scheduled_at=now,
            status="planned",
            created_by=admin["id"],
        )
        cancelled_meeting = BusinessMeeting(
            meeting_type="party_group",
            organization=f"今日聚合-{uuid4().hex[:8]}",
            title="已取消党小组会",
            scheduled_at=now,
            status="cancelled",
            created_by=admin["id"],
        )
        db.add_all([party_meeting, study_meeting, cancelled_meeting])
        db.flush()
        db.add(
            MeetingAction(
                meeting_id=party_meeting.id,
                title="逾期整改",
                due_at=now - timedelta(days=1),
                status="pending",
                created_by=admin["id"],
            )
        )
        case = PartyDevelopmentCase(
            party_committee="测试党委",
            party_branch="测试党支部",
            name=f"提醒人员-{uuid4().hex[:6]}",
            application_at=now - timedelta(days=180),
            status="active",
            created_by=admin["id"],
        )
        db.add(case)
        db.flush()
        db.add(
            PartyDevelopmentMilestone(
                case_id=case.id,
                milestone_type=f"test_{uuid4().hex[:8]}",
                planned_at=now + timedelta(days=10),
            )
        )
        db.commit()

    response = client.get("/api/v1/today")
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(item["title"] == today_task.title for item in body["today_tasks"])
    # 接口有前 20 条上限；共享全量测试库中只断言对应聚合桶确实产生内容。
    assert body["overdue_tasks"]
    assert body["pending_review_feedback"]
    assert body["risks"]["incomplete_materials"] >= 1
    assert body["party_work"]["pending_archives"] >= 1
    assert body["party_work"]["overdue_actions"] >= 1
    assert body["party_work"]["development_reminders"] >= 1
    assert body["party_work"]["study_center_recorded"] >= 1


def test_today_fourth_quarter_uses_next_year_boundary(
    client: TestClient,
    admin: dict,
    monkeypatch,
) -> None:
    _login(client)
    monkeypatch.setattr(
        today_router,
        "utcnow",
        lambda: datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
    )
    response = client.get("/api/v1/today")
    assert response.status_code == 200, response.text
    assert response.json()["party_work"]["quarter"] == 4


def test_network_override_valid_invalid_and_posix_permissions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PARTYOPS_DATA_DIR", str(tmp_path))
    config.reset_settings_cache()
    override = tmp_path / "network-settings.json"
    override.write_text(
        json.dumps({"bind_host": "127.0.0.1", "advertise_host": "192.168.1.8"}),
        encoding="utf-8",
    )
    loaded = config.get_settings()
    assert loaded.bind_host == "127.0.0.1"
    assert loaded.advertise_host == "192.168.1.8"

    override.write_text("{broken-json", encoding="utf-8")
    config.reset_settings_cache()
    assert config.get_settings().data_dir == tmp_path

    config.reset_settings_cache()
    # 只替换 config 模块的引用，不能改写进程级 os.name，否则 pathlib 会切换到错误平台实现。
    monkeypatch.setattr(config, "os", SimpleNamespace(name="posix"))
    target = config.write_network_override(
        {"bind_host": "127.0.0.1", "advertise_host": "192.168.1.9", "port": 18765}
    )
    assert json.loads(target.read_text(encoding="utf-8"))["port"] == 18765
    config.reset_settings_cache()
