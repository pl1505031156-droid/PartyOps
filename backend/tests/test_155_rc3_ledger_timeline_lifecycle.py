"""1.4.5-rc.3 台账导入、统一时间轴和非破坏性删除回归。"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app import models  # noqa: F401  # 注册全部 SQLAlchemy 元数据供迁移往返测试使用。
from app.config import get_settings
from app.database import Base, db_runtime
from app.enums import ModelPackStatus, TaskStatus
from app.ledger_imports import parse_table
from app.models import AIModelActivation, AIModelPack, Task
from app.problems import ProblemException


def _xlsx(headers: list[str], rows: list[list[object]], title: str = "台账") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = title
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _inspect(
    client: TestClient,
    content: bytes,
    *,
    target_type: str,
    target_id: str | None = None,
) -> dict:
    data = {"target_type": target_type}
    if target_id:
        data["target_id"] = target_id
    response = client.post(
        "/api/v1/ledger-imports/inspect",
        data=data,
        files={"file": ("本地台账.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _mapping(job: dict, *, custom_headers: set[str] | None = None) -> dict:
    custom_headers = custom_headers or set()
    columns = []
    for column in job["profile"]["selected"]["columns"]:
        header = column["header"]
        suggestion = column.get("suggestion") or {}
        if header in custom_headers:
            columns.append(
                {
                    "source_column": header,
                    "action": "create",
                    "create_label": header,
                    "create_type": "text",
                    "confirmed": True,
                }
            )
        else:
            assert suggestion.get("target_field"), column
            columns.append(
                {
                    "source_column": header,
                    "action": "map",
                    "target_field": suggestion["target_field"],
                    "confirmed": True,
                }
            )
    return {
        "sheet_name": job["sheet_name"],
        "header_row": job["header_row"],
        "mappings": columns,
        "version": job["version"],
    }


def _map_validate_commit(
    client: TestClient,
    job: dict,
    *,
    custom_headers: set[str] | None = None,
) -> dict:
    mapped = client.patch(
        f"/api/v1/ledger-imports/{job['id']}/mapping",
        json=_mapping(job, custom_headers=custom_headers),
    )
    assert mapped.status_code == 200, mapped.text
    validated = client.post(
        f"/api/v1/ledger-imports/{job['id']}/validate",
        json={"version": mapped.json()["version"], "row_actions": {}},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["error_rows"] == 0
    committed = client.post(
        f"/api/v1/ledger-imports/{job['id']}/commit",
        json={
            "version": validated.json()["version"],
            "confirm_shared_storage": True,
            "confirm_new_fields": bool(custom_headers),
            "row_actions": {},
        },
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["status"] == "committed"
    return committed.json()


def test_party_ledger_import_timeline_and_safe_undo(
    client: TestClient, admin: dict
) -> None:
    content = _xlsx(
        [
            "所属党委",
            "所属党支部",
            "姓名",
            "出生日期",
            "提交入党申请时间",
            "谈话时间",
            "联系电话",
        ],
        [["中共导入测试党委", "第一支部", "导入测试甲", "1990-05-06", "2026-01-02", "2026-01-10", "13800000000"]],
        "发展党员",
    )
    job = _inspect(client, content, target_type="party_development")
    committed = _map_validate_commit(client, job, custom_headers={"联系电话"})

    cases = client.get(
        "/api/v1/party-development/cases",
        params={"party_committee": "中共导入测试党委"},
    ).json()
    created = next(item for item in cases if item["name"] == "导入测试甲")
    assert created["extra_fields"]
    timeline = client.get(
        f"/api/v1/party-development/cases/{created['id']}/timeline"
    )
    assert timeline.status_code == 200, timeline.text
    assert any(
        item["milestone_type"] == "conversation"
        and item["visual_state"] == "completed"
        for item in timeline.json()["timeline"]
    )
    assert any(item["visual_state"] != "completed" for item in timeline.json()["timeline"])

    undone = client.post(
        f"/api/v1/ledger-imports/{job['id']}/undo",
        json={"version": committed["version"]},
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    archived = client.get(
        "/api/v1/party-development/cases",
        params={"case_status": "archived", "party_committee": "中共导入测试党委"},
    ).json()
    assert any(item["id"] == created["id"] for item in archived)


def test_party_case_deletion_impact_archive_and_restore(
    client: TestClient, admin: dict
) -> None:
    created_response = client.post(
        "/api/v1/party-development/cases",
        json={
            "party_committee": "中共生命周期测试党委",
            "party_branch": "第二支部",
            "name": "生命周期测试乙",
            "application_date": "2026-02-01",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    impact = client.get(
        f"/api/v1/party-development/cases/{created['id']}/deletion-impact"
    )
    assert impact.status_code == 200
    assert impact.json()["physical_delete"] is False
    assert impact.json()["recoverable"] is True

    archived = client.request(
        "DELETE",
        f"/api/v1/party-development/cases/{created['id']}",
        headers={"If-Match": str(created["version"])},
        json={"reason": "重复建立，保留审计后归档"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "archived"
    active_ids = {
        item["id"] for item in client.get("/api/v1/party-development/cases").json()
    }
    assert created["id"] not in active_ids

    restored = client.post(
        f"/api/v1/party-development/cases/{created['id']}/restore",
        headers={"If-Match": str(archived.json()["version"])},
        json={"reason": "核对后确认需要继续办理"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"


def test_archive_ledger_adapter_commit_and_undo(
    client: TestClient, admin: dict
) -> None:
    categories = client.get("/api/v1/archives/categories").json()
    category = next(item for item in categories if item["name"] == "事业编年度考核")
    content = _xlsx(
        ["年度", "姓名", "人员编号", "考核结果", "标题"],
        [[2026, "考核测试丙", "KHTEST-001", "优秀", "2026 年度考核档案"]],
        "年度考核",
    )
    job = _inspect(
        client, content, target_type="archive", target_id=category["id"]
    )
    committed = _map_validate_commit(client, job)
    records = client.get(
        "/api/v1/archives/records",
        params={"category_id": category["id"], "archive_year": 2026},
    )
    assert records.status_code == 200, records.text
    record = next(item for item in records.json() if item["person_identifier"] == "KHTEST-001")

    undone = client.post(
        f"/api/v1/ledger-imports/{job['id']}/undo",
        json={"version": committed["version"]},
    )
    assert undone.status_code == 200, undone.text
    voided = client.get(
        "/api/v1/archives/records",
        params={"category_id": category["id"], "archive_year": 2026, "status": "voided"},
    )
    assert any(item["id"] == record["id"] for item in voided.json())


def test_malicious_office_container_and_formula_are_rejected() -> None:
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("xl/vbaProject.bin", b"not-executed")
    with pytest.raises(ProblemException) as caught:
        parse_table(package.getvalue(), ".xlsx")
    assert caught.value.code == "LEDGER_MACRO_BLOCKED"


def test_0024_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "upgrade-0024.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        config.attributes["connection"] = connection
        command.stamp(config, "0024")
        command.downgrade(config, "0023")
        assert "ledger_import_jobs" not in inspect(connection).get_table_names()
        command.upgrade(config, "0024")
    inspector = inspect(engine)
    assert {
        "ledger_import_jobs",
        "ledger_import_mapping_templates",
        "ledger_import_changes",
        "party_development_progress_events",
    }.issubset(inspector.get_table_names())
    assert {"extra_fields", "import_batch_id"}.issubset(
        {
            item["name"]
            for item in inspector.get_columns("party_development_cases")
        }
    )
    assert {"archived_at", "archived_by", "archive_reason"}.issubset(
        {item["name"] for item in inspector.get_columns("workflow_templates")}
    )
    assert {
        "status_before_archive",
        "archived_at",
        "archived_by",
        "archive_reason",
    }.issubset(
        {item["name"] for item in inspector.get_columns("business_meetings")}
    )
    assert {"archived_at", "archived_by", "archive_reason"}.issubset(
        {item["name"] for item in inspector.get_columns("work_journal_entries")}
    )
    for table in ("meeting_topics", "meeting_attendees", "meeting_actions"):
        assert {"archived_at", "archived_by", "archive_reason"}.issubset(
            {item["name"] for item in inspector.get_columns(table)}
        )
    assert "task_status_before_archive" in {
        item["name"] for item in inspector.get_columns("meeting_actions")
    }
    assert {
        "deleted_at",
        "deleted_by",
        "delete_reason",
        "purge_after",
        "version",
    }.issubset({item["name"] for item in inspector.get_columns("backup_runs")})


def test_empty_database_can_replay_full_migration_chain(tmp_path: Path) -> None:
    """历史 0001 使用动态元数据，后续迁移仍必须能幂等重放到 rc.6。"""

    database = tmp_path / "empty-to-0026.sqlite3"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    inspector = inspect(engine)
    assert "ledger_import_jobs" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "0026"
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert "ledger_import_jobs" not in inspect(engine).get_table_names()


def test_meeting_document_and_study_plan_have_recoverable_lifecycle(
    client: TestClient, admin: dict
) -> None:
    template = client.get("/api/v1/workflow-templates").json()[0]
    meeting_response = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共生命周期审查委员会",
            "title": "可恢复归档测试会议",
            "workflow_template_id": template["id"],
            "owner_id": admin["id"],
        },
    )
    assert meeting_response.status_code == 201, meeting_response.text
    meeting = meeting_response.json()
    document_response = client.post(
        "/api/v1/business-documents",
        json={
            "meeting_id": meeting["id"],
            "document_type": "agenda",
            "title": "归档测试议程",
            "content": {"blocks": [{"type": "paragraph", "text": "测试正文"}]},
        },
    )
    assert document_response.status_code == 201, document_response.text
    document = document_response.json()

    document_impact = client.get(
        f"/api/v1/business-documents/{document['id']}/deletion-impact"
    )
    assert document_impact.status_code == 200
    assert document_impact.json()["revisions"] == 1
    archived_document = client.request(
        "DELETE",
        f"/api/v1/business-documents/{document['id']}",
        headers={"If-Match": str(document["version"])},
        json={"reason": "重复议程，归档后保留修订历史"},
    )
    assert archived_document.status_code == 200, archived_document.text
    assert not any(
        row["id"] == document["id"]
        for row in client.get("/api/v1/business-documents").json()
    )
    restored_document = client.post(
        f"/api/v1/business-documents/{document['id']}/restore",
        headers={"If-Match": str(archived_document.json()["version"])},
        json={"reason": "核对后恢复继续协作"},
    )
    assert restored_document.status_code == 200, restored_document.text

    impact = client.get(
        f"/api/v1/business-meetings/{meeting['id']}/deletion-impact"
    )
    assert impact.status_code == 200
    assert impact.json()["steps"] == meeting["progress"]["total"]
    archived_meeting = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting['id']}",
        headers={"If-Match": str(meeting["version"])},
        json={"reason": "会议重复建立，归档整套筹备记录"},
    )
    assert archived_meeting.status_code == 200, archived_meeting.text
    assert archived_meeting.json()["status"] == "archived"
    assert not any(
        row["id"] == meeting["id"]
        for row in client.get("/api/v1/business-meetings").json()
    )
    restored_meeting = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/restore",
        headers={"If-Match": str(archived_meeting.json()["version"])},
        json={"reason": "确认会议仍需继续办理"},
    )
    assert restored_meeting.status_code == 200, restored_meeting.text
    assert restored_meeting.json()["status"] == "planned"

    plan_response = client.post(
        "/api/v1/study-center/plans",
        json={
            "organization": "中共生命周期审查委员会",
            "year": 2027,
            "title": "2027 年中心组学习计划",
            "group_leader_id": admin["id"],
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    plan_impact = client.get(
        f"/api/v1/study-center/plans/{plan['id']}/deletion-impact"
    )
    assert plan_impact.status_code == 200
    archived_plan = client.request(
        "DELETE",
        f"/api/v1/study-center/plans/{plan['id']}",
        headers={"If-Match": str(plan["version"])},
        json={"reason": "年度计划重复建立，先归档待核对"},
    )
    assert archived_plan.status_code == 200, archived_plan.text
    restored_plan = client.post(
        f"/api/v1/study-center/plans/{plan['id']}/restore",
        headers={"If-Match": str(archived_plan.json()["version"])},
        json={"reason": "核对组织年度后恢复"},
    )
    assert restored_plan.status_code == 200, restored_plan.text


def test_party_work_specialized_lists_expose_archived_meetings(
    client: TestClient, admin: dict
) -> None:
    meeting_response = client.post(
        "/api/v1/party-life/meetings",
        json={
            "meeting_type": "branch_members",
            "organization": "中共生命周期列表支部",
            "title": "支委会归档筛选测试",
            "owner_id": admin["id"],
        },
    )
    assert meeting_response.status_code == 201, meeting_response.text
    meeting = meeting_response.json()
    archived = client.request(
        "DELETE",
        f"/api/v1/business-meetings/{meeting['id']}",
        headers={"If-Match": str(meeting["version"])},
        json={"reason": "重复建立，归档后从在办台账隐藏"},
    )
    assert archived.status_code == 200, archived.text
    active_rows = client.get(
        "/api/v1/party-life/meetings",
        params={"organization": "中共生命周期列表支部"},
    ).json()
    archived_rows = client.get(
        "/api/v1/party-life/meetings",
        params={
            "organization": "中共生命周期列表支部",
            "lifecycle": "archived",
        },
    ).json()
    assert not any(item["id"] == meeting["id"] for item in active_rows)
    assert any(item["id"] == meeting["id"] for item in archived_rows)


def test_topic_and_manual_journal_have_impact_archive_restore(
    client: TestClient, admin: dict
) -> None:
    topic_response = client.post(
        "/api/v1/topics",
        json={"name": "生命周期专题空间", "description": "只归档容器，不删除原件"},
    )
    assert topic_response.status_code == 201, topic_response.text
    topic = topic_response.json()
    topic_impact = client.get(f"/api/v1/topics/{topic['id']}/deletion-impact")
    assert topic_impact.status_code == 200
    assert topic_impact.json()["physical_delete"] is False
    archived_topic = client.request(
        "DELETE",
        f"/api/v1/topics/{topic['id']}",
        headers={"If-Match": str(topic["version"])},
        json={"reason": "专题已结束，保留全部关联记录"},
    )
    assert archived_topic.status_code == 200, archived_topic.text
    assert any(
        item["id"] == topic["id"]
        for item in client.get(
            "/api/v1/topics", params={"lifecycle": "archived"}
        ).json()
    )
    restored_topic = client.post(
        f"/api/v1/topics/{topic['id']}/restore",
        headers={"If-Match": str(archived_topic.json()["version"])},
        json={"reason": "复核后恢复继续办理"},
    )
    assert restored_topic.status_code == 200, restored_topic.text
    assert restored_topic.json()["active"] is True

    journal_response = client.post(
        "/api/v1/work-journal",
        json={"title": "可恢复人工日志", "content": "人工补充的办理说明"},
    )
    assert journal_response.status_code == 201, journal_response.text
    journal = journal_response.json()
    journal_impact = client.get(
        f"/api/v1/work-journal/{journal['id']}/deletion-impact"
    )
    assert journal_impact.status_code == 200
    assert journal_impact.json()["physical_delete"] is False
    archived_journal = client.request(
        "DELETE",
        f"/api/v1/work-journal/{journal['id']}",
        headers={"If-Match": str(journal["version"])},
        json={"reason": "重复人工日志，归档保留修订链"},
    )
    assert archived_journal.status_code == 200, archived_journal.text
    assert archived_journal.json()["archived_at"]
    assert any(
        item["id"] == journal["id"]
        for item in client.get(
            "/api/v1/work-journal", params={"lifecycle": "archived"}
        ).json()
    )
    restored_journal = client.post(
        f"/api/v1/work-journal/{journal['id']}/restore",
        headers={"If-Match": str(archived_journal.json()["version"])},
        json={"reason": "核对后恢复人工记录"},
    )
    assert restored_journal.status_code == 200, restored_journal.text
    assert restored_journal.json()["archived_at"] is None


def test_meeting_children_have_recoverable_remove_and_restore(
    client: TestClient, admin: dict
) -> None:
    template = client.get("/api/v1/workflow-templates").json()[0]
    meeting_response = client.post(
        "/api/v1/business-meetings",
        json={
            "meeting_type": "party_committee",
            "organization": "中共子记录生命周期委员会",
            "title": "议题出席和落实项可恢复测试",
            "workflow_template_id": template["id"],
            "owner_id": admin["id"],
        },
    )
    assert meeting_response.status_code == 201, meeting_response.text
    meeting = meeting_response.json()
    topic_response = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/topics",
        json={
            "title": "误录后可恢复议题",
            "review_result": "审议通过",
            "amount": "100.00",
            "reviewed": True,
            "amount_confirmed": True,
        },
    )
    attendee_response = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/attendees",
        json={
            "display_name": "参会测试人员",
            "attendance_status": "present",
            "voting_eligible": True,
        },
    )
    action_response = client.post(
        f"/api/v1/business-meetings/{meeting['id']}/actions",
        json={"title": "可恢复落实项", "create_task": True},
    )
    for response in (topic_response, attendee_response, action_response):
        assert response.status_code == 201, response.text

    topic = topic_response.json()
    attendee = attendee_response.json()
    action = action_response.json()
    cases = (
        ("topics", topic, "重复议题，移入已移除区"),
        ("attendees", attendee, "人员误录，保留审计后移除"),
        ("actions", action, "落实项重复，关联事项同步归档"),
    )
    for kind, item, reason in cases:
        impact = client.get(
            f"/api/v1/business-meetings/{meeting['id']}/{kind}/{item['id']}/deletion-impact"
        )
        assert impact.status_code == 200, impact.text
        assert impact.json()["physical_delete"] is False
        archived = client.request(
            "DELETE",
            f"/api/v1/business-meetings/{meeting['id']}/{kind}/{item['id']}",
            headers={"If-Match": str(item["version"])},
            json={"reason": reason},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["archived_at"]
        item.update(archived.json())

    assert client.get(
        f"/api/v1/business-meetings/{meeting['id']}/attendees"
    ).json() == []
    assert client.get(
        f"/api/v1/business-meetings/{meeting['id']}/actions"
    ).json() == []
    assert len(
        client.get(
            f"/api/v1/business-meetings/{meeting['id']}/attendees",
            params={"lifecycle": "archived"},
        ).json()
    ) == 1
    with db_runtime.session_factory() as db:
        linked_task = db.get(Task, action["task_id"])
        assert linked_task and linked_task.status == TaskStatus.ARCHIVED

    for kind, item, _reason in cases:
        restored = client.post(
            f"/api/v1/business-meetings/{meeting['id']}/{kind}/{item['id']}/restore",
            headers={"If-Match": str(item["version"])},
            json={"reason": "复核后确认需要恢复"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["archived_at"] is None
    with db_runtime.session_factory() as db:
        linked_task = db.get(Task, action["task_id"])
        assert linked_task and linked_task.status == TaskStatus.IN_PROGRESS


def test_model_pack_uninstall_blocks_active_then_removes_managed_files(
    client: TestClient, admin: dict
) -> None:
    suffix = uuid.uuid4().hex
    install_key = f"uninstall-{suffix}"
    filename = f"uninstall-{suffix}.partyops-modelpack"
    settings = get_settings()
    runtime_root = settings.models_dir / install_key
    package_path = settings.models_dir / "packages" / filename
    runtime_root.mkdir(parents=True)
    package_path.parent.mkdir(parents=True, exist_ok=True)
    (runtime_root / "runtime.bin").write_bytes(b"runtime")
    package_path.write_bytes(b"signed-package")
    with db_runtime.session_factory() as db:
        pack = AIModelPack(
            name="可卸载签名包测试",
            version="2.0.3",
            model_id=f"uninstall-{suffix}",
            architecture="amd64",
            filename=filename,
            install_key=install_key,
            sha256="0" * 64,
            size_bytes=14,
            manifest={"files": {}},
            capabilities=["intent_router"],
            min_runtime_version="1.4.5",
            signature_valid=True,
            status=ModelPackStatus.INSTALLED,
            created_by=admin["id"],
        )
        db.add(pack)
        db.flush()
        pack_id = pack.id
        db.add(
            AIModelActivation(
                capability="intent_router",
                model_pack_id=pack.id,
                activated_by=admin["id"],
            )
        )
        db.commit()

    blocked = client.delete(f"/api/v1/admin/ai/model-packs/{pack_id}")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "MODEL_PACK_ACTIVE"
    assert runtime_root.exists() and package_path.exists()
    deactivated = client.delete("/api/v1/admin/ai/model-activations/intent_router")
    assert deactivated.status_code == 200, deactivated.text
    removed = client.delete(f"/api/v1/admin/ai/model-packs/{pack_id}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["uninstalled"] is True
    assert not runtime_root.exists()
    assert not package_path.exists()
    with db_runtime.session_factory() as db:
        assert db.get(AIModelPack, pack_id) is None


def test_backup_trash_preserves_last_recovery_point_and_can_restore(
    client: TestClient, admin: dict
) -> None:
    first_response = client.post("/api/v1/backups")
    second_response = client.post("/api/v1/backups")
    assert first_response.status_code == 201, first_response.text
    assert second_response.status_code == 201, second_response.text
    first = first_response.json()
    impact = client.get(
        f"/api/v1/admin/backups/{first['id']}/deletion-impact"
    )
    assert impact.status_code == 200, impact.text
    assert impact.json()["physical_delete"] is False
    assert impact.json()["remaining_completed_backups"] >= 1
    deleted = client.request(
        "DELETE",
        f"/api/v1/admin/backups/{first['id']}",
        headers={"If-Match": str(first["version"])},
        json={"reason": "重复手动备份，移入回收站待清理"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_at"]
    assert deleted.json()["purge_after"]
    assert client.get(f"/api/v1/backups/{first['id']}/download").status_code == 404
    deleted_rows = client.get(
        "/api/v1/backups", params={"lifecycle": "deleted"}
    ).json()
    assert any(item["id"] == first["id"] for item in deleted_rows)
    restored = client.post(
        f"/api/v1/admin/backups/{first['id']}/restore",
        headers={"If-Match": str(deleted.json()["version"])},
        json={"reason": "管理员复核后恢复备份"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["deleted_at"] is None


def test_archive_link_can_be_removed_without_deleting_either_side(
    client: TestClient, admin: dict
) -> None:
    category = next(
        item
        for item in client.get("/api/v1/archives/categories").json()
        if item["record_mode"] == "document"
    )
    record_response = client.post(
        "/api/v1/archives/records",
        json={
            "category_id": category["id"],
            "archive_year": 2026,
            "title": "关联移除生命周期测试档案",
        },
    )
    knowledge_response = client.post(
        "/api/v1/knowledge",
        json={"title": "关联移除测试知识", "body": "原知识条目必须保留。"},
    )
    assert record_response.status_code == 201, record_response.text
    assert knowledge_response.status_code == 201, knowledge_response.text
    record = record_response.json()
    knowledge = knowledge_response.json()
    linked = client.post(
        f"/api/v1/archives/records/{record['id']}/links",
        json={
            "entity_type": "knowledge",
            "entity_id": knowledge["id"],
            "relation": "reference",
        },
    )
    assert linked.status_code == 201, linked.text
    linked_record = linked.json()
    link = linked_record["links"][0]
    removed = client.delete(
        f"/api/v1/archives/records/{record['id']}/links/{link['id']}",
        headers={"If-Match": str(linked_record["version"])},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["links"] == []
    assert client.get(f"/api/v1/archives/records/{record['id']}").status_code == 200
    assert any(
        item["id"] == knowledge["id"]
        for item in client.get("/api/v1/knowledge").json()
    )


def test_legacy_archive_template_can_be_deactivated_and_restored(
    client: TestClient, admin: dict
) -> None:
    created_response = client.post(
        "/api/v1/archive-templates",
        json={
            "name": f"归档模板生命周期-{uuid.uuid4().hex[:8]}",
            "description": "只停用模板，不改写既有档案。",
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    impact = client.get(
        f"/api/v1/archive-templates/{created['id']}/deletion-impact"
    )
    assert impact.status_code == 200, impact.text
    assert impact.json()["physical_delete"] is False
    deactivated = client.request(
        "DELETE",
        f"/api/v1/archive-templates/{created['id']}",
        headers={"If-Match": str(created["version"])},
        json={"reason": "模板重复，停用并保留历史"},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False
    restored = client.post(
        f"/api/v1/archive-templates/{created['id']}/restore",
        headers={"If-Match": str(deactivated.json()["version"])},
        json={"reason": "核对后恢复模板"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["active"] is True
