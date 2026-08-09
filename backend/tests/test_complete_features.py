"""完整功能补齐后的业务、运维与灾备契约测试。"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import client_agent, main as main_module, networking, scheduler, setup_wizard
from app.backups import _ensure_data_child, restore_backup, verify_backup
from app.config import get_settings
from app.database import db_runtime
from app.models import BackupRun, User
from app.problems import ProblemException

from .conftest import create_task


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    assert response.status_code == 200


def current_task(client: TestClient, task_id: str) -> dict:
    response = client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_frozen_frontend_directory_uses_pyinstaller_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main_module.settings, "frontend_dist", None)
    monkeypatch.setattr(main_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main_module.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert main_module.frontend_directory() == (tmp_path / "frontend").resolve()


def task_action(client: TestClient, task: dict, action: str, note: str = "") -> dict:
    response = client.post(
        f"/api/v1/tasks/{task['id']}/actions",
        json={"action": action, "note": note},
        headers={"If-Match": str(task["version"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_project_subtasks_collaboration_and_logical_archive(
    client: TestClient, admin: dict, staff: dict
) -> None:
    parent = create_task(
        client,
        admin["id"],
        title="专项评估项目",
        task_type="project",
        steps=[],
        materials=[],
        category="党建检查",
        tags=["迎检", "年度"],
    )
    invalid_child = client.post(
        "/api/v1/tasks",
        json={
            "title": "无效子任务",
            "owner_id": admin["id"],
            "parent_task_id": "missing",
        },
    )
    assert invalid_child.status_code == 422
    child = create_task(
        client,
        staff["id"],
        title="村级材料核验",
        parent_task_id=parent["id"],
        category="党建检查",
        steps=[],
        materials=[{"category": "evidence", "name": "核验说明", "required": True}],
    )
    parent = current_task(client, parent["id"])
    assert parent["subtasks"][0]["id"] == child["id"]
    blocked = client.post(
        f"/api/v1/tasks/{parent['id']}/actions",
        json={"action": "complete", "note": ""},
        headers={"If-Match": str(parent["version"])},
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "SUBTASKS_INCOMPLETE"

    participant = client.post(
        f"/api/v1/tasks/{parent['id']}/participants",
        json={"user_id": staff["id"], "role": "collaborator"},
        headers={"If-Match": str(parent["version"])},
    )
    assert participant.status_code == 200
    parent = participant.json()
    collaborator = next(
        item for item in parent["participants"] if item["role"] == "collaborator"
    )
    removed = client.delete(
        f"/api/v1/tasks/{parent['id']}/participants/{collaborator['id']}",
        headers={"If-Match": str(parent["version"])},
    )
    assert removed.status_code == 200
    parent = removed.json()
    owner_relation = next(
        item for item in parent["participants"] if item["role"] == "owner"
    )
    assert (
        client.delete(
            f"/api/v1/tasks/{parent['id']}/participants/{owner_relation['id']}",
            headers={"If-Match": str(parent["version"])},
        ).json()["code"]
        == "OWNER_REMOVE_DENIED"
    )

    added_step = client.post(
        f"/api/v1/tasks/{parent['id']}/steps",
        json={"title": "汇总问题清单", "assignee_id": admin["id"]},
        headers={"If-Match": str(parent["version"])},
    )
    assert added_step.status_code == 201
    step = added_step.json()
    changed_step = client.patch(
        f"/api/v1/tasks/{parent['id']}/steps/{step['id']}",
        json={"title": "形成整改问题清单", "done": True},
        headers={"If-Match": str(step["version"])},
    )
    assert changed_step.status_code == 200
    assert changed_step.json()["done"] is True
    step = changed_step.json()
    deleted_step = client.delete(
        f"/api/v1/tasks/{parent['id']}/steps/{step['id']}",
        headers={"If-Match": str(step["version"])},
    )
    assert deleted_step.status_code == 200
    parent = deleted_step.json()

    assert (
        client.post(
            f"/api/v1/tasks/{parent['id']}/comments",
            json={"body": "错误回复", "parent_id": "missing"},
        ).status_code
        == 422
    )
    comment = client.post(
        f"/api/v1/tasks/{parent['id']}/comments",
        json={"body": "已确认检查口径"},
        headers={"If-Match": str(parent["version"])},
    )
    assert comment.status_code == 201
    parent = current_task(client, parent["id"])
    reply = client.post(
        f"/api/v1/tasks/{parent['id']}/comments",
        json={"body": "收到，按此整理", "parent_id": comment.json()["id"]},
        headers={"If-Match": str(parent["version"])},
    )
    assert reply.status_code == 201

    child = current_task(client, child["id"])
    child = task_action(client, child, "accept")
    child = task_action(client, child, "complete")
    blocked_archive = client.post(
        f"/api/v1/tasks/{child['id']}/actions",
        json={"action": "archive", "note": ""},
        headers={"If-Match": str(child["version"])},
    )
    assert blocked_archive.status_code == 409
    material = child["materials"][0]
    assert (
        client.patch(
            f"/api/v1/tasks/{child['id']}/materials/{material['id']}",
            json={"not_applicable": True, "reason": ""},
            headers={"If-Match": str(material["version"])},
        ).status_code
        == 422
    )
    patched = client.patch(
        f"/api/v1/tasks/{child['id']}/materials/{material['id']}",
        json={"not_applicable": True, "reason": "本期未开展现场核验"},
        headers={"If-Match": str(material["version"])},
    )
    assert patched.status_code == 200
    child = current_task(client, child["id"])
    child = task_action(client, child, "archive")
    assert child["status"] == "archived"

    parent = current_task(client, parent["id"])
    parent = task_action(client, parent, "complete")
    parent = task_action(client, parent, "archive")
    snapshots = client.get(
        f"/api/v1/tasks/{parent['id']}/archive-snapshots"
    ).json()
    assert snapshots and snapshots[0]["manifest"]["task"]["id"] == parent["id"]
    package = client.get(f"/api/v1/tasks/{parent['id']}/archive-package")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "manifest.json" in archive.namelist()


def test_material_version_filename_search_and_final_rules(
    client: TestClient, admin: dict
) -> None:
    task = create_task(
        client,
        admin["id"],
        title="材料版本与文件名检索",
        steps=[],
        materials=[],
        category="材料报送",
    )
    material_response = client.post(
        f"/api/v1/tasks/{task['id']}/materials",
        json={"category": "final", "name": "实际报送稿", "required": True},
        headers={"If-Match": str(task["version"])},
    )
    material = material_response.json()
    task = current_task(client, task["id"])
    wrong_stage = client.post(
        f"/api/v1/tasks/{task['id']}/materials/{material['id']}/versions",
        data={"stage": "draft", "is_final": "true", "note": ""},
        files={"file": ("党建台账终稿.xlsx", b"same-content", "application/octet-stream")},
        headers={"If-Match": str(task["version"])},
    )
    assert wrong_stage.status_code == 422
    uploaded = client.post(
        f"/api/v1/tasks/{task['id']}/materials/{material['id']}/versions",
        data={"stage": "submitted", "is_final": "true", "note": "领导审定后报送"},
        files={"file": ("党建台账终稿.xlsx", b"same-content", "application/octet-stream")},
        headers={"If-Match": str(task["version"])},
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["materials"][0]["versions"][0]
    assert version["original_name"] == "党建台账终稿.xlsx"
    assert version["note"] == "领导审定后报送"
    found = client.get(
        "/api/v1/search",
        params={"file_name": "台账终稿", "category": "材料报送"},
    )
    assert any(item["id"] == task["id"] for item in found.json()["items"])
    assert client.get("/api/v1/exports/tasks.xlsx?kind=材料目录").status_code == 200
    assert client.get("/api/v1/exports/tasks.xlsx?kind=材料缺项清单").status_code == 200
    assert client.get("/api/v1/exports/tasks.xlsx?kind=催报清单").status_code == 200
    assert client.get("/api/v1/exports/tasks.docx?kind=交接清单").status_code == 200


def test_conflict_draft_reminders_and_user_directory(
    client: TestClient, admin: dict, staff: dict
) -> None:
    task = create_task(client, admin["id"], steps=[], materials=[])
    updated = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"title": "同事先保存的标题"},
        headers={"If-Match": str(task["version"])},
    ).json()
    conflict = client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"title": "我的冲突草稿"},
        headers={"If-Match": str(task["version"])},
    )
    assert conflict.status_code == 409
    draft_id = conflict.json()["draft_id"]
    drafts = client.get("/api/v1/conflicts").json()
    assert any(item["id"] == draft_id for item in drafts)
    applied = client.post(
        f"/api/v1/conflicts/{draft_id}/apply",
        headers={"If-Match": str(updated["version"])},
    )
    assert applied.status_code == 200
    assert applied.json()["title"] == "我的冲突草稿"
    assert not any(item["id"] == draft_id for item in client.get("/api/v1/conflicts").json())

    directory = client.get("/api/v1/users")
    assert directory.status_code == 200
    assert {admin["id"], staff["id"]}.issubset(
        {item["id"] for item in directory.json()}
    )
    preference = client.get("/api/v1/reminders/preferences").json()
    assert (
        client.patch(
            "/api/v1/reminders/preferences",
            json={"advance_days": 5},
            headers={"If-Match": "999"},
        ).status_code
        == 409
    )
    saved = client.patch(
        "/api/v1/reminders/preferences",
        json={
            "advance_days": 5,
            "remind_feedback": False,
            "remind_materials": False,
        },
        headers={"If-Match": str(preference["version"])},
    )
    assert saved.status_code == 200
    buckets = client.get("/api/v1/dashboard").json()["buckets"]
    assert next(item for item in buckets if item["key"] == "three_days")["label"] == "5日内到期"


def test_template_recurrence_knowledge_and_contacts_are_maintainable(
    client: TestClient, admin: dict, staff: dict
) -> None:
    contact = client.post(
        "/api/v1/contacts",
        json={
            "name": "王老师",
            "organization": "业务科室",
            "phone": "12345",
            "note": "负责报送口径",
        },
    ).json()
    assert client.get("/api/v1/contacts", params={"keyword": "业务"}).json()
    changed_contact = client.patch(
        f"/api/v1/contacts/{contact['id']}",
        json={**{key: contact[key] for key in ("name", "organization", "phone", "note")}, "note": "负责最新口径"},
        headers={"If-Match": str(contact["version"])},
    )
    assert changed_contact.status_code == 200

    entry = client.post(
        "/api/v1/knowledge",
        json={"title": "交接说明", "category": "交接", "body": "先核验终稿。"},
    ).json()
    changed_entry = client.patch(
        f"/api/v1/knowledge/{entry['id']}",
        json={"title": "交接说明", "category": "交接", "body": "先核验终稿和回执。"},
        headers={"If-Match": str(entry["version"])},
    )
    assert changed_entry.status_code == 200

    template_payload = {
        "name": "可维护专项模板",
        "category": "专项",
        "task_type": "standard",
        "description": "初始说明",
        "steps": ["确认口径"],
        "materials": [{"category": "final", "name": "终稿", "required": True}],
    }
    template = client.post("/api/v1/templates", json=template_payload).json()
    update_payload = {
        **template_payload,
        "description": "更新后的说明",
        "steps": ["确认口径", "审核报送"],
        "active": True,
    }
    changed = client.patch(
        f"/api/v1/templates/{template['id']}",
        json=update_payload,
        headers={"If-Match": str(template["version"])},
    )
    assert changed.status_code == 200
    assert len(changed.json()["steps"]) == 2
    assert (
        client.patch(
            f"/api/v1/templates/{template['id']}",
            json=update_payload,
            headers={"If-Match": str(template["version"])},
        ).status_code
        == 409
    )

    past = datetime.now(timezone.utc) - timedelta(minutes=2)
    rule = client.post(
        "/api/v1/recurrences",
        json={
            "name": "专项月度复用",
            "template_id": template["id"],
            "owner_id": admin["id"],
            "kind": "monthly",
            "custom_days": None,
            "internal_lead_days": 3,
            "next_run_at": past.isoformat(),
            "notes": "沿用上期统计口径",
            "contact_ids": [contact["id"]],
        },
    ).json()
    generated_ids = client.post("/api/v1/recurrences/run-due").json()
    assert generated_ids
    generated = current_task(client, generated_ids[-1])
    assert generated["experience_notes"] == "沿用上期统计口径"
    assert generated["contact_ids"] == [contact["id"]]
    learned = client.patch(
        f"/api/v1/tasks/{generated['id']}",
        json={"experience_notes": "上期反馈：照片命名需含日期"},
        headers={"If-Match": str(generated["version"])},
    )
    assert learned.status_code == 200
    updated_rule = next(
        item for item in client.get("/api/v1/recurrences").json() if item["id"] == rule["id"]
    )
    due_again = client.patch(
        f"/api/v1/recurrences/{rule['id']}",
        # 同一次计划必须幂等；用另一个已到期时间验证下一实例复用上期经验。
        json={"next_run_at": (past + timedelta(seconds=1)).isoformat()},
        headers={"If-Match": str(updated_rule["version"])},
    )
    assert due_again.status_code == 200
    second_ids = client.post("/api/v1/recurrences/run-due").json()
    second = current_task(client, second_ids[-1])
    assert second["experience_notes"] == "上期反馈：照片命名需含日期"
    assert len(second["steps"]) == 2
    patched_rule = client.patch(
        f"/api/v1/recurrences/{rule['id']}",
        json={"active": False, "notes": "规则暂缓"},
        headers={"If-Match": str(due_again.json()["version"] + 1)},
    )
    assert patched_rule.status_code == 200

    assert (
        client.delete(
            f"/api/v1/knowledge/{entry['id']}",
            headers={"If-Match": str(changed_entry.json()["version"])},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/contacts/{contact['id']}",
            headers={"If-Match": str(changed_contact.json()["version"])},
        ).status_code
        == 200
    )

    client.post(
        "/api/v1/auth/login",
        json={"username": "staff", "password": "PartyOps@2026"},
    )
    denied = client.post("/api/v1/templates", json={**template_payload, "name": "越权模板"})
    assert denied.status_code == 403
    login_admin(client)


def test_admin_user_backup_pairing_diagnostics_and_audit(
    client: TestClient, admin: dict
) -> None:
    created_user = client.post(
        "/api/v1/admin/users",
        json={
            "username": "temporary",
            "display_name": "临时协同",
            "password": "PartyOps@2026",
            "role": "staff",
        },
    ).json()
    changed_user = client.patch(
        f"/api/v1/admin/users/{created_user['id']}",
        json={"display_name": "备用协同", "active": True},
        headers={"If-Match": str(created_user["version"])},
    )
    assert changed_user.status_code == 200
    assert (
        client.patch(
            f"/api/v1/admin/users/{created_user['id']}",
            json={"active": False},
            headers={"If-Match": str(created_user["version"])},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/admin/users/{created_user['id']}/reset-password",
            json={"password": "PartyOps@2027"},
        ).status_code
        == 200
    )
    admin_current = next(
        item for item in client.get("/api/v1/admin/users").json() if item["id"] == admin["id"]
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{admin['id']}",
            json={"active": False},
            headers={"If-Match": str(admin_current["version"])},
        ).json()["code"]
        == "SELF_DISABLE_DENIED"
    )

    backup = client.post("/api/v1/backups").json()
    verify = client.post(f"/api/v1/admin/backups/{backup['id']}/verify")
    assert verify.status_code == 200 and verify.json()["valid"]
    source = get_settings().backups_dir / backup["filename"]
    imported = client.post(
        "/api/v1/admin/backups/import",
        files={"file": ("终端副本.partyops-backup", source.read_bytes(), "application/zip")},
    )
    assert imported.status_code == 201

    pairing_response = client.post(
        "/api/v1/admin/pairings", json={"name": "备用终端"}
    )
    pairing = pairing_response.json()
    assert pairing["config"]["pairing_token"] == pairing["token"]
    assert client.get("/api/v1/admin/pairings").status_code == 200
    latest = client.get(
        "/api/v1/backups/latest",
        headers={"X-PartyOps-Pairing": pairing["token"]},
    )
    assert latest.status_code == 200
    etag = latest.headers["etag"]
    not_modified = client.get(
        "/api/v1/backups/latest",
        headers={
            "X-PartyOps-Pairing": pairing["token"],
            "If-None-Match": etag,
        },
    )
    assert not_modified.status_code == 304
    assert (
        client.delete(f"/api/v1/admin/pairings/{pairing['id']}").status_code == 200
    )
    assert (
        client.get(
            "/api/v1/backups/latest",
            headers={"X-PartyOps-Pairing": pairing["token"]},
        ).status_code
        == 401
    )
    diagnostics = client.get("/api/v1/admin/diagnostics").json()
    assert diagnostics["counts"]["tasks"] > 0
    assert client.get("/api/v1/admin/logs?lines=20").status_code == 200
    assert client.get("/api/v1/admin/audit.csv").content.startswith(b"\xef\xbb\xbf")
    assert client.get("/api/v1/admin/audit", params={"action": "backup"}).status_code == 200


def test_backup_rejects_extra_files_and_newer_schema(
    client: TestClient, admin: dict, tmp_path: Path
) -> None:
    backup = client.post("/api/v1/backups").json()
    source = get_settings().backups_dir / backup["filename"]
    extra = tmp_path / "extra.partyops-backup"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(extra, "w") as output:
        for info in original.infolist():
            output.writestr(info, original.read(info.filename))
        output.writestr("unexpected.bin", b"extra")
    with pytest.raises(ProblemException) as error:
        verify_backup(extra)
    assert error.value.code == "BACKUP_EXTRA_FILES"

    too_new = tmp_path / "new.partyops-backup"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(too_new, "w") as output:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest["schema_version"] = "9999"
                data = json.dumps(manifest).encode()
            output.writestr(info, data)
    with pytest.raises(ProblemException) as newer:
        verify_backup(too_new)
    assert newer.value.code == "BACKUP_SCHEMA_TOO_NEW"


def make_minimal_backup(path: Path) -> str:
    database = b"db"
    manifest = {
        "format": "partyops-backup",
        "files": [
            {
                "path": "database/partyops.db",
                "size": len(database),
                "sha256": hashlib.sha256(database).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("database/partyops.db", database)
        archive.writestr("manifest.json", json.dumps(manifest))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_client_agent_networking_and_setup_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backup = tmp_path / "minimal.partyops-backup"
    digest = make_minimal_backup(backup)
    assert client_agent.verify_local_backup(backup)["format"] == "partyops-backup"
    assert client_agent._response_filename("attachment; filename=../safe.partyops-backup") == "safe.partyops-backup"
    assert client_agent._response_filename("attachment; filename*=UTF-8''%E5%A4%87%E4%BB%BD.partyops-backup").endswith(".partyops-backup")
    assert client_agent._response_filename("") == "PartyOps-latest.partyops-backup"
    config = {
        "host_url": "http://192.168.1.20:18765",
        "pairing_token": "token",
        "backup_dir": str(tmp_path / "copies"),
    }
    assert client_agent.validate_config(config)[0].startswith("http://")
    with pytest.raises(ValueError):
        client_agent.validate_config({**config, "host_url": "https://user:pass@example.com"})
    assert client_agent.run(tmp_path / "missing.json", once=True) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert client_agent.run(bad, once=True) == 2

    class Response:
        headers = {
            "Content-Disposition": 'attachment; filename="copy.partyops-backup"',
            "X-PartyOps-SHA256": digest,
        }

        def __enter__(self):
            self.handle = backup.open("rb")
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def read(self, size: int):
            return self.handle.read(size)

    monkeypatch.setattr(client_agent.urllib.request, "urlopen", lambda *_a, **_k: Response())
    pulled = client_agent.pull_backup("http://192.168.1.20:18765", "token", tmp_path / "copies")
    assert pulled and pulled.exists()

    monkeypatch.setattr(networking.socket, "getaddrinfo", lambda *_a, **_k: [(None, None, None, None, ("192.168.20.5", 0))])
    addresses = networking.discover_lan_addresses()
    assert "192.168.20.5" in addresses
    networking.validate_bind_host("office-host", True)
    networking.validate_bind_host("127.0.0.1", True)
    with pytest.raises(RuntimeError):
        networking.validate_bind_host("0.0.0.0", True)
    with pytest.raises(RuntimeError):
        networking.validate_bind_host("8.8.8.8", True)
    assert networking.service_url("0.0.0.0", 18765) == "http://127.0.0.1:18765"

    monkeypatch.setattr(setup_wizard, "config_root", lambda: tmp_path / "config")
    monkeypatch.setattr(setup_wizard, "discover_lan_addresses", lambda: ["192.168.20.5"])
    host_config = setup_wizard.write_host_config(
        "192.168.20.5", 18765, tmp_path / "data"
    )
    env = setup_wizard.load_host_environment(host_config)
    assert env["PARTYOPS_HOST"] == "192.168.20.5"
    with pytest.raises(ValueError):
        setup_wizard.write_host_config("8.8.8.8", 18765, tmp_path / "data")
    with pytest.raises(ValueError):
        setup_wizard.write_host_config("192.168.20.5", 80, tmp_path / "data")
    client_config = setup_wizard.write_client_config(
        "http://192.168.20.5:18765", "token", tmp_path / "copies", 600
    )
    assert json.loads(client_config.read_text(encoding="utf-8"))["pairing_token"] == "token"
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: tmp_path)
    (tmp_path / "start.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    host_autostart = setup_wizard.install_host_autostart(host_config)
    assert host_autostart is not None
    assert "党建智办主机服务" in host_autostart.read_text(encoding="utf-8")
    assert str(host_config.resolve()) in host_autostart.read_text(encoding="utf-8")
    assert "配置为主机" in setup_wizard.render_page("csrf")
    assert "成功" in setup_wizard.render_page("csrf", message="成功")
    assert "错误" in setup_wizard.render_page("csrf", error="错误")


def test_setup_wizard_browser_flow_and_launch_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    real_launch_host = setup_wizard.launch_host
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(
        setup_wizard,
        "write_host_config",
        lambda *_args: tmp_path / "partyops.env",
    )
    monkeypatch.setattr(
        setup_wizard,
        "launch_host",
        lambda _path: "http://127.0.0.1:18765",
    )
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(setup_wizard.run_wizard(True)), daemon=True
    )
    thread.start()
    for _ in range(100):
        if opened:
            break
        time.sleep(0.01)
    assert opened
    with setup_wizard.urllib.request.urlopen(opened[0], timeout=5) as response:
        page = response.read().decode("utf-8")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    payload = setup_wizard.urllib.parse.urlencode(
        {
            "csrf": csrf,
            "mode": "host",
            "host": "127.0.0.1",
            "port": "18765",
            "data_dir": str(tmp_path / "data"),
        }
    ).encode()
    request = setup_wizard.urllib.request.Request(
        opened[0], data=payload, method="POST"
    )
    with setup_wizard.urllib.request.urlopen(request, timeout=5) as response:
        assert "主机已启动" in response.read().decode("utf-8")
    thread.join(timeout=5)
    assert result == [0]

    executable = tmp_path / "partyops"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(setup_wizard, "launch_host", real_launch_host)
    assert setup_wizard._executable("partyops") == executable
    commands: list[tuple[list[str], Path, dict[str, str] | None]] = []
    monkeypatch.setattr(
        setup_wizard,
        "_spawn",
        lambda command, log_path, env=None: commands.append(
            (command, log_path, env)
        ),
    )
    config = tmp_path / "real.env"
    config.write_text(
        "\n".join(
            [
                "PARTYOPS_HOST=127.0.0.1",
                "PARTYOPS_PORT=18765",
                f"PARTYOPS_DATA_DIR={tmp_path.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    assert setup_wizard.launch_host(config) == "http://127.0.0.1:18765"
    assert commands

    client_executable = tmp_path / "partyops-client"
    client_executable.write_text("", encoding="utf-8")
    client_config = tmp_path / "client.json"
    client_config.write_text(
        json.dumps(
            {
                "host_url": "http://127.0.0.1:18765",
                "pairing_token": "token",
                "backup_dir": str(tmp_path / "copies"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(
        setup_wizard,
        "config_root",
        lambda: tmp_path / "config" / "partyops",
    )
    assert setup_wizard.launch_client(client_config) == "http://127.0.0.1:18765"
    assert any("partyops-client" in item[0][0] for item in commands)
    autostart = tmp_path / "config" / "autostart" / "partyops-client.desktop"
    assert "partyops-client" in autostart.read_text(encoding="utf-8")

    class HealthResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status":"ok"}'

    monkeypatch.setattr(
        setup_wizard.urllib.request, "urlopen", lambda *_a, **_k: HealthResponse()
    )
    assert setup_wizard.check_host("http://127.0.0.1:18765")["status"] == "ok"

    attempted_urls: list[str] = []

    def https_after_plain_http_disconnect(request, **_kwargs):
        attempted_urls.append(request.full_url)
        if request.full_url.startswith("http://"):
            raise setup_wizard.http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )
        return HealthResponse()

    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        https_after_plain_http_disconnect,
    )
    resolved, health = setup_wizard.resolve_host_url(
        "http://192.168.20.5:18765"
    )
    assert resolved == "https://192.168.20.5:18765"
    assert health["status"] == "ok"
    assert attempted_urls == [
        "http://192.168.20.5:18765/api/v1/health",
        "https://192.168.20.5:18765/api/v1/health",
    ]

    monkeypatch.setattr(
        setup_wizard.urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(
            setup_wizard.urllib.error.URLError("offline")
        ),
    )
    with pytest.raises(ValueError):
        setup_wizard.check_host("http://127.0.0.1:18765")


def test_setup_wizard_persists_enrolled_device_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """入网成功后必须完整保存三份证书和终端配置，不能让向导空响应退出。"""

    root = tmp_path / "config" / "partyops"
    installed_ca: list[Path] = []
    monkeypatch.setattr(setup_wizard, "config_root", lambda: root)
    monkeypatch.setattr(
        setup_wizard,
        "install_internal_ca",
        lambda path: installed_ca.append(path),
    )

    config_path = setup_wizard.write_device_config(
        "https://192.168.20.5:18765",
        {
            "device_id": "device-1",
            "device_token": "device-token",
            "agent_url": "https://192.168.20.5:18766",
            "_private_key_pem": "PRIVATE KEY",
            "certificate_pem": "DEVICE CERTIFICATE",
            "ca_certificate_pem": "CA CERTIFICATE",
        },
        tmp_path / "copies",
        device_name="协同电脑",
    )

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["device_id"] == "device-1"
    assert config["agent_url"] == "https://192.168.20.5:18766"
    assert Path(config["key_file"]).read_text(encoding="utf-8") == "PRIVATE KEY"
    assert (
        Path(config["certificate_file"]).read_text(encoding="utf-8")
        == "DEVICE CERTIFICATE"
    )
    assert Path(config["ca_file"]).read_text(encoding="utf-8") == "CA CERTIFICATE"
    assert installed_ca == [root / "pki" / "ca.pem"]


def test_setup_wizard_installs_ca_for_current_desktop_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """内部 CA 必须同时指定当前桌面账号，并把授权失败明确反馈给向导。"""

    helper = tmp_path / "install-internal-ca.sh"
    helper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text("CA", encoding="utf-8")
    monkeypatch.setattr(setup_wizard.sys, "platform", "linux")
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(setup_wizard.getpass, "getuser", lambda: "desktop-user")
    commands: list[list[str]] = []

    def successful_run(command, **kwargs):
        commands.append(command)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_wizard.subprocess, "run", successful_run)
    setup_wizard.install_internal_ca(ca_path)
    assert commands == [
        [
            "pkexec",
            str(helper),
            "--desktop-user",
            "desktop-user",
            str(ca_path.resolve()),
        ]
    ]

    monkeypatch.setattr(
        setup_wizard.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 6, "", "浏览器证书库写入失败"
        ),
    )
    with pytest.raises(ValueError, match="浏览器安全证书安装未完成"):
        setup_wizard.install_internal_ca(ca_path)


def test_desktop_launcher_repairs_ca_trust_once_for_existing_configs() -> None:
    """已配置电脑升级后也必须补装 CA，并用指纹标记避免每次启动重复授权。"""

    launcher = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "uos"
        / "desktop-launcher.sh"
    ).read_text(encoding="utf-8")
    helper = (
        Path(__file__).resolve().parents[2]
        / "packaging"
        / "uos"
        / "install-internal-ca.sh"
    ).read_text(encoding="utf-8")
    assert "ensure_ca_trust" in launcher
    assert "ca-trusted.sha256" in launcher
    assert 'pkexec "$CA_HELPER" --desktop-user "$(id -un)"' in launcher
    assert "ca-trusted.sha256" in helper
    assert "CA_FINGERPRINT" in helper
    assert "certutil -A" in helper


def test_setup_wizard_requires_first_device_heartbeat_before_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """配置成功提示前必须验证设备安全通道，失败时给出端口诊断而不是假成功。"""

    executable = tmp_path / "partyops-client"
    executable.write_text("", encoding="utf-8")
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "https://192.168.20.5:18765",
                "agent_url": "https://192.168.20.5:18766",
                "device_id": "device-1",
                "device_token": "device-token",
                "backup_dir": str(tmp_path / "copies"),
            }
        ),
        encoding="utf-8",
    )
    spawned: list[list[str]] = []
    heartbeat_results = iter([False, True])
    monkeypatch.setattr(setup_wizard, "runtime_root", lambda: tmp_path)
    monkeypatch.setattr(
        setup_wizard,
        "_spawn",
        lambda command, *_args, **_kwargs: spawned.append(command),
    )
    monkeypatch.setattr(
        setup_wizard,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: next(heartbeat_results),
    )
    monkeypatch.setattr(setup_wizard.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        setup_wizard,
        "create_browser_launch_url",
        lambda host, *_args: host,
    )

    assert (
        setup_wizard.launch_client(config_path)
        == "https://192.168.20.5:18765"
    )
    assert spawned and spawned[0][-1] == "--no-open-browser"

    monkeypatch.setattr(
        setup_wizard,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: False,
    )
    with pytest.raises(ValueError, match="设备端口 18766"):
        setup_wizard.launch_client(config_path)


def test_problem_handler_hides_unexpected_details(client: TestClient, monkeypatch) -> None:
    from app.routers import auth

    monkeypatch.setattr(auth.db_runtime, "validate_capabilities", lambda: (_ for _ in ()).throw(RuntimeError("C:/secret/path")))
    safe_client = TestClient(client.app, raise_server_exceptions=False)
    response = safe_client.get("/api/v1/health")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text


def _write_client_manifest_backup(
    path: Path,
    *,
    manifest_format: str = "partyops-backup",
    item_path: str = "database/partyops.db",
    declared_size: int | None = None,
    declared_hash: str | None = None,
) -> None:
    content = b"client-backup"
    manifest = {
        "format": manifest_format,
        "files": [
            {
                "path": item_path,
                "size": len(content) if declared_size is None else declared_size,
                "sha256": hashlib.sha256(content).hexdigest()
                if declared_hash is None
                else declared_hash,
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(item_path, content)
        archive.writestr("manifest.json", json.dumps(manifest))


def test_client_agent_rejects_corruption_and_handles_http_states(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invalid = tmp_path / "not-zip.partyops-backup"
    invalid.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="有效备份"):
        client_agent.verify_local_backup(invalid)

    wrong_format = tmp_path / "wrong-format.partyops-backup"
    _write_client_manifest_backup(wrong_format, manifest_format="other")
    with pytest.raises(ValueError, match="格式"):
        client_agent.verify_local_backup(wrong_format)

    traversal = tmp_path / "traversal.partyops-backup"
    _write_client_manifest_backup(traversal, item_path="../database.db")
    with pytest.raises(ValueError, match="非法路径"):
        client_agent.verify_local_backup(traversal)

    wrong_size = tmp_path / "wrong-size.partyops-backup"
    _write_client_manifest_backup(wrong_size, declared_size=999)
    with pytest.raises(ValueError, match="大小"):
        client_agent.verify_local_backup(wrong_size)

    wrong_hash = tmp_path / "wrong-hash.partyops-backup"
    _write_client_manifest_backup(wrong_hash, declared_hash="0" * 64)
    with pytest.raises(ValueError, match="哈希"):
        client_agent.verify_local_backup(wrong_hash)

    with pytest.raises(ValueError, match="配对令牌"):
        client_agent.validate_config(
            {
                "host_url": "http://192.168.1.20:18765",
                "pairing_token": "",
                "backup_dir": str(tmp_path),
            }
        )

    unchanged = urllib.error.HTTPError(
        "http://host/api/v1/backups/latest", 304, "not modified", {}, None
    )
    monkeypatch.setattr(
        client_agent.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(unchanged),
    )
    assert client_agent.pull_backup("http://host", "token", tmp_path / "copies") is None

    no_backup = urllib.error.HTTPError(
        "http://host/api/v1/backups/latest", 404, "not found", {}, None
    )
    monkeypatch.setattr(
        client_agent.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(no_backup),
    )
    assert client_agent.pull_backup("http://host", "token", tmp_path / "copies") is None

    failure = urllib.error.HTTPError(
        "http://host/api/v1/backups/latest", 500, "failed", {}, None
    )
    monkeypatch.setattr(
        client_agent.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(urllib.error.HTTPError):
        client_agent.pull_backup("http://host", "token", tmp_path / "copies")

    valid = tmp_path / "valid.partyops-backup"
    _write_client_manifest_backup(valid)

    class HashMismatchResponse:
        headers = {
            "Content-Disposition": 'attachment; filename="copy.partyops-backup"',
            "X-PartyOps-SHA256": "f" * 64,
        }

        def __enter__(self):
            self.handle = valid.open("rb")
            return self

        def __exit__(self, *_args):
            self.handle.close()

        def read(self, size: int):
            return self.handle.read(size)

    monkeypatch.setattr(
        client_agent.urllib.request, "urlopen", lambda *_a, **_k: HashMismatchResponse()
    )
    with pytest.raises(ValueError, match="主机校验值"):
        client_agent.pull_backup("http://host", "token", tmp_path / "copies")
    assert not list((tmp_path / "copies").glob("*.part"))

    config = tmp_path / "client.json"
    config.write_text(
        json.dumps(
            {
                "host_url": "http://host:18765",
                "pairing_token": "token",
                "backup_dir": str(tmp_path / "copies"),
                "open_browser": True,
            }
        ),
        encoding="utf-8",
    )
    opened: list[str] = []
    monkeypatch.setattr(client_agent.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        client_agent,
        "pull_backup",
        lambda *_args: (_ for _ in ()).throw(ValueError("损坏")),
    )
    assert client_agent.run(config, once=True) == 1
    assert opened == ["http://host:18765"]

    monkeypatch.setattr(client_agent, "pull_backup", lambda *_args: None)
    monkeypatch.setattr(
        client_agent.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop-loop")),
    )
    with pytest.raises(RuntimeError, match="stop-loop"):
        client_agent.run(config, once=False)

    monkeypatch.setattr(client_agent, "host_reachable", lambda _host: True)
    assert client_agent.run(config, once=True) == 0


def test_client_agent_heartbeat_and_command_schedules_are_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """心跳续期后仍必须立即轮询命令，防止在线设备永远收不到协同操作。"""

    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps(
            {
                "host_url": "https://host:18765",
                "agent_url": "https://host:18766",
                "device_token": "device-token",
                "backup_dir": str(tmp_path / "copies"),
                "open_browser": False,
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda *_args, **_kwargs: calls.append("heartbeat") or True,
    )
    monkeypatch.setattr(
        client_agent,
        "sync_shared_roots",
        lambda *_args, **_kwargs: calls.append("roots"),
    )
    monkeypatch.setattr(
        client_agent,
        "poll_device_commands",
        lambda *_args, **_kwargs: [
            {"id": "command-1", "type": "rotate_certificate", "payload": {}}
        ],
    )
    monkeypatch.setattr(
        client_agent,
        "process_device_command",
        lambda *_args, **_kwargs: calls.append("command") or True,
    )
    monkeypatch.setattr(client_agent, "pull_backup", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        client_agent,
        "poll_desktop_notifications",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        client_agent.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(RuntimeError("stop-loop")),
    )

    with pytest.raises(RuntimeError, match="stop-loop"):
        client_agent.run(config_path, once=False)

    assert calls[:3] == ["heartbeat", "roots", "command"]


def test_client_agent_heartbeat_worker_continues_during_slow_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """慢扫描、传输或更新期间，独立心跳线程仍应维持设备在线。"""

    class StopAfterOneHeartbeat:
        def __init__(self) -> None:
            self.wait_count = 0

        def wait(self, _seconds: float) -> bool:
            self.wait_count += 1
            return self.wait_count > 1

    heartbeats: list[str] = []
    monkeypatch.setattr(
        client_agent,
        "send_device_heartbeat",
        lambda host, *_args, **_kwargs: heartbeats.append(host) or True,
    )

    client_agent._heartbeat_loop(  # noqa: SLF001 - 回归验证内部调度契约。
        StopAfterOneHeartbeat(),  # type: ignore[arg-type]
        "https://host:18766",
        "device-token",
        {"device_token": "device-token"},
    )

    assert heartbeats == ["https://host:18766"]


def test_successful_device_update_restarts_agent_after_ack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """更新后的旧 Agent 必须自动换成新进程，版本门禁才能解除。"""

    config_path = tmp_path / "client.json"
    events: list[str] = []
    monkeypatch.setattr(
        client_agent,
        "apply_update_command",
        lambda *_args, **_kwargs: {"ok": True, "message": "设备升级完成"},
    )
    monkeypatch.setattr(
        client_agent,
        "ack_device_command",
        lambda *_args, **_kwargs: events.append("ack") or True,
    )
    monkeypatch.setattr(
        client_agent,
        "_restart_agent_after_update",
        lambda path: events.append(f"restart:{path.name}"),
    )

    assert client_agent.process_device_command(
        "https://host:18766",
        "device-token",
        {"id": "update-1", "type": "apply_update", "payload": {}},
        {},
        config_path,
    )
    assert events == ["ack", "restart:client.json"]


def test_backup_manifest_failure_modes_and_restore_rollback(
    client: TestClient, admin: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = get_settings()
    with pytest.raises(RuntimeError, match="超出数据目录"):
        _ensure_data_child(settings.data_dir)

    original_task = create_task(
        client,
        admin["id"],
        title="恢复失败后必须保留的事项",
        steps=[],
        materials=[],
    )
    backup = client.post("/api/v1/backups").json()
    source = settings.backups_dir / backup["filename"]

    def rewrite(
        target: Path,
        mutate_manifest=None,
        mutate_database=None,
    ) -> None:
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as output:
            for info in original.infolist():
                data = original.read(info.filename)
                if info.filename == "manifest.json" and mutate_manifest:
                    manifest = json.loads(data)
                    mutate_manifest(manifest)
                    data = json.dumps(manifest).encode()
                if info.filename == "database/partyops.db" and mutate_database:
                    data = mutate_database(data)
                output.writestr(info, data)

    duplicate = tmp_path / "duplicate.partyops-backup"

    def duplicate_manifest(manifest: dict) -> None:
        manifest["files"].append(dict(manifest["files"][0]))

    rewrite(duplicate, duplicate_manifest)
    with pytest.raises(ProblemException) as duplicate_error:
        verify_backup(duplicate)
    assert duplicate_error.value.code == "BACKUP_MANIFEST_INVALID"

    missing_database = tmp_path / "missing-db.partyops-backup"

    def remove_database(manifest: dict) -> None:
        manifest["files"] = [
            item for item in manifest["files"] if item["path"] != "database/partyops.db"
        ]

    rewrite(missing_database, remove_database)
    with pytest.raises(ProblemException) as missing_error:
        verify_backup(missing_database)
    assert missing_error.value.code == "BACKUP_DATABASE_MISSING"

    hash_mismatch = tmp_path / "hash-mismatch.partyops-backup"

    def wrong_hash(manifest: dict) -> None:
        manifest["files"][0]["sha256"] = "0" * 64

    rewrite(hash_mismatch, wrong_hash)
    with pytest.raises(ProblemException) as hash_error:
        verify_backup(hash_mismatch)
    assert hash_error.value.code == "BACKUP_HASH_MISMATCH"

    corrupt_database = tmp_path / "corrupt-db.partyops-backup"

    def corrupt_manifest_and_database(manifest: dict) -> None:
        database = next(
            item for item in manifest["files"] if item["path"] == "database/partyops.db"
        )
        database["size"] = 12
        database["sha256"] = hashlib.sha256(b"not-a-sqlite").hexdigest()

    rewrite(
        corrupt_database,
        corrupt_manifest_and_database,
        lambda _data: b"not-a-sqlite",
    )
    with pytest.raises(Exception):
        verify_backup(corrupt_database)

    monkeypatch.setattr(
        "app.backups.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=1, used=1, free=0),
    )
    with pytest.raises(ProblemException) as space_error:
        restore_backup(source, admin["id"])
    assert space_error.value.code == "RESTORE_SPACE_INSUFFICIENT"

    monkeypatch.undo()
    previous_title = current_task(client, original_task["id"])["title"]
    real_validate = db_runtime.validate_capabilities
    validate_calls = 0

    def fail_once():
        nonlocal validate_calls
        validate_calls += 1
        if validate_calls == 1:
            raise RuntimeError("模拟恢复启动校验失败")
        return real_validate()

    monkeypatch.setattr(db_runtime, "validate_capabilities", fail_once)
    with pytest.raises(RuntimeError, match="模拟恢复"):
        restore_backup(source, admin["id"])
    monkeypatch.setattr(db_runtime, "validate_capabilities", real_validate)
    db_runtime.validate_capabilities()
    assert current_task(client, original_task["id"])["title"] == previous_title


@pytest.mark.asyncio
async def test_scheduler_runs_backup_recurrence_and_isolates_failures(
    client: TestClient, admin: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now()
    settings = get_settings()
    monkeypatch.setattr(settings, "backup_hour", now.hour)
    monkeypatch.setattr(settings, "backup_minute", max(0, now.minute - 1))
    with db_runtime.session_factory() as db:
        for record in db.scalars(
            select(BackupRun).where(BackupRun.kind == "automatic")
        ).all():
            db.delete(record)
        db.commit()

    backups: list[str] = []
    recurrences: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "create_backup",
        lambda _db, _actor, kind: backups.append(kind),
    )
    monkeypatch.setattr(
        scheduler,
        "run_due_rules",
        lambda _db, actor: recurrences.append(actor.id) or [],
    )

    class OneCycleEvent:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        async def wait(self) -> None:
            self.stopped = True

    event = OneCycleEvent()
    await scheduler.scheduler_loop(event)  # type: ignore[arg-type]
    assert backups == ["automatic"]
    assert recurrences == [admin["id"]]

    class TimeoutCycleEvent:
        calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            return self.calls > 1

        async def wait(self) -> None:
            return None

    async def force_timeout(_awaitable, timeout):
        del timeout
        if hasattr(_awaitable, "close"):
            _awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(scheduler.asyncio, "wait_for", force_timeout)
    await scheduler.scheduler_loop(TimeoutCycleEvent())  # type: ignore[arg-type]

    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("database offline")

    monkeypatch.setattr(scheduler.db_runtime, "session_factory", BrokenFactory())
    await scheduler.scheduler_loop(TimeoutCycleEvent())  # type: ignore[arg-type]


def test_setup_wizard_client_error_flow_and_process_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert setup_wizard.config_root().is_dir()
    monkeypatch.setattr(setup_wizard.sys, "frozen", True, raising=False)
    monkeypatch.setattr(setup_wizard.sys, "executable", str(tmp_path / "wizard"))
    assert setup_wizard.runtime_root() == tmp_path
    monkeypatch.setattr(setup_wizard.sys, "frozen", False, raising=False)

    with pytest.raises(ValueError, match="拉取间隔"):
        setup_wizard.write_client_config(
            "http://127.0.0.1:18765", "token", tmp_path / "copies", 10
        )
    with pytest.raises(FileNotFoundError):
        setup_wizard._executable("missing-program")

    popen_calls: list[tuple[list[str], dict]] = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            popen_calls.append((command, kwargs))

    monkeypatch.setattr(setup_wizard.subprocess, "Popen", FakeProcess)
    setup_wizard._spawn(["fixed-program"], tmp_path / "logs" / "launcher.log")
    assert popen_calls[0][0] == ["fixed-program"]
    assert (tmp_path / "logs" / "launcher.log").exists()

    opened: list[str] = []
    monkeypatch.setattr(setup_wizard.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        setup_wizard,
        "resolve_host_url",
        lambda value, *_args: (value, {"status": "ok"}),
    )
    monkeypatch.setattr(
        setup_wizard,
        "enroll_device",
        lambda *_args, **_kwargs: {
            "device_id": "device-1",
            "device_token": "device-token",
        },
    )
    monkeypatch.setattr(
        setup_wizard,
        "write_device_config",
        lambda *_args, **_kwargs: tmp_path / "client.json",
    )
    monkeypatch.setattr(
        setup_wizard,
        "launch_client",
        lambda _path: "http://192.168.1.20:18765",
    )
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(setup_wizard.run_wizard(True)), daemon=True
    )
    thread.start()
    for _ in range(100):
        if opened:
            break
        time.sleep(0.01)
    with setup_wizard.urllib.request.urlopen(opened[0], timeout=5) as response:
        page = response.read().decode("utf-8")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    bad_request = setup_wizard.urllib.request.Request(
        opened[0],
        data=setup_wizard.urllib.parse.urlencode(
            {"csrf": "expired", "mode": "unknown"}
        ).encode(),
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as bad_error:
        setup_wizard.urllib.request.urlopen(bad_request, timeout=5)
    assert bad_error.value.code == 400
    assert "页面已失效" in bad_error.value.read().decode("utf-8")

    missing_name_request = setup_wizard.urllib.request.Request(
        opened[0],
        data=setup_wizard.urllib.parse.urlencode(
            {
                "csrf": csrf,
                "mode": "client",
                "host_url": "http://192.168.1.20:18765",
                "token": "pairing-token",
                "backup_dir": str(tmp_path / "copies"),
            }
        ).encode(),
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as missing_name_error:
        setup_wizard.urllib.request.urlopen(missing_name_request, timeout=5)
    assert missing_name_error.value.code == 400
    assert "必须填写本机设备名称" in missing_name_error.value.read().decode(
        "utf-8"
    )

    payload = setup_wizard.urllib.parse.urlencode(
        {
            "csrf": csrf,
            "mode": "client",
            "host_url": "http://192.168.1.20:18765",
            "token": "pairing-token",
            "device_name": "协同电脑",
            "backup_dir": str(tmp_path / "copies"),
        }
    ).encode()
    request = setup_wizard.urllib.request.Request(
        opened[0], data=payload, method="POST"
    )
    with setup_wizard.urllib.request.urlopen(request, timeout=5) as response:
        assert "协同终端已启动" in response.read().decode("utf-8")
    thread.join(timeout=5)
    assert result == [0]


def test_error_contract_and_missing_resource_branches(
    client: TestClient, admin: dict
) -> None:
    validation = client.post("/api/v1/auth/login", json={})
    assert validation.status_code == 422
    assert validation.headers["content-type"].startswith("application/problem+json")
    assert "username" in validation.json()["fields"]

    assert client.patch(
        "/api/v1/admin/users/missing", json={"display_name": "无效"}
    ).status_code == 404
    assert client.post(
        "/api/v1/admin/users/missing/reset-password",
        json={"password": "PartyOps@2028"},
    ).status_code == 404

    created_user = client.post(
        "/api/v1/admin/users",
        json={
            "username": "disable-me",
            "display_name": "停用测试",
            "password": "PartyOps@2026",
            "role": "staff",
        },
    ).json()
    assert client.patch(
        f"/api/v1/admin/users/{created_user['id']}",
        json={"active": False},
    ).status_code == 428
    assert client.patch(
        f"/api/v1/admin/users/{created_user['id']}",
        json={"active": False},
        headers={"If-Match": "bad"},
    ).status_code == 400

    anonymous = TestClient(client.app)
    assert anonymous.get("/api/v1/backups/not-found/download").status_code == 401
    assert client.post("/api/v1/admin/backups/not-found/verify").status_code == 404
    assert client.delete("/api/v1/admin/pairings/not-found").status_code == 404

    assert client.patch(
        "/api/v1/templates/not-found",
        json={
            "name": "不存在",
            "category": "",
            "task_type": "standard",
            "description": "",
            "steps": [],
            "materials": [],
            "active": True,
        },
        headers={"If-Match": "1"},
    ).status_code == 404
    assert client.patch(
        "/api/v1/recurrences/not-found",
        json={"active": False},
        headers={"If-Match": "1"},
    ).status_code == 404
    assert client.patch(
        "/api/v1/knowledge/not-found",
        json={"title": "无效", "category": "测试", "body": "无效内容"},
        headers={"If-Match": "1"},
    ).status_code == 404
    assert client.delete(
        "/api/v1/contacts/not-found", headers={"If-Match": "1"}
    ).status_code == 404
