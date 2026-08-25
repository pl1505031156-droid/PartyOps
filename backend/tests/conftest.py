"""隔离的 API 测试环境。"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATA = (
    Path(__file__).resolve().parents[1]
    / ".test-data"
    / f"pytest-{os.getpid()}-{uuid.uuid4().hex}"
)
os.environ["PARTYOPS_DATA_DIR"] = str(TEST_DATA)
os.environ["PARTYOPS_ENVIRONMENT"] = "test"
os.environ["PARTYOPS_SEED_DEMO"] = "false"
os.environ["PARTYOPS_STRICT_SQLITE"] = "false"
os.environ["PARTYOPS_BACKUP_HOUR"] = "25"

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolate_device_context(client: TestClient):
    """设备版本门禁 Cookie 不得污染共享客户端中的其他业务用例。"""
    client.cookies.delete("partyops_device_context")
    yield
    client.cookies.delete("partyops_device_context")


@pytest.fixture(scope="session")
def admin(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/bootstrap/host",
        json={
            "username": "admin",
            "display_name": "系统管理员",
            "password": "PartyOps@2026",
        },
    )
    assert response.status_code == 201, response.text
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "PartyOps@2026"},
    )
    assert login.status_code == 200, login.text
    return login.json()


@pytest.fixture(scope="session")
def staff(client: TestClient, admin: dict) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "staff",
            "display_name": "协同人员",
            "password": "PartyOps@2026",
            "role": "staff",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_task(
    client: TestClient,
    owner_id: str,
    **overrides,
) -> dict:
    payload = {
        "title": "基层党建闭环测试事项",
        "description": "验证任务状态、材料和并发控制。",
        "task_type": "standard",
        "sensitivity": "normal",
        "priority": "normal",
        "source": "自动化测试",
        "source_kind": "manual",
        "formal_due_at": "2026-08-01T10:00:00+08:00",
        "internal_due_at": "2026-07-31T17:00:00+08:00",
        "owner_id": owner_id,
        "reviewer_id": None,
        "collaborator_ids": [],
        "steps": [{"title": "核对材料目录"}],
        "materials": [{"category": "final", "name": "实际报送稿", "required": True}],
    }
    payload.update(overrides)
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
