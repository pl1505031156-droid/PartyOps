"""不同身份的上手闭环与引导进度并发分支。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.enums import UserRole
from app.models import OnboardingProgress
from app.problems import ProblemException
from app.routers import guidance
from app.schemas import OnboardingProgressPatch


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class _Db:
    def __init__(self, *, scalars=None, scalar=None):
        self.scalars_queue = list(scalars or [])
        self.scalar_queue = list(scalar or [])
        self.added = []

    def scalars(self, _statement):
        return _Rows(self.scalars_queue.pop(0) if self.scalars_queue else [])

    def scalar(self, _statement):
        return self.scalar_queue.pop(0) if self.scalar_queue else None

    def add(self, value):
        if isinstance(value, OnboardingProgress):
            value.completed_steps = list(value.completed_steps or [])
            value.dismissed = bool(value.dismissed)
            value.version = int(value.version or 1)
            value.updated_at = value.updated_at or datetime.now(UTC)
        self.added.append(value)

    def flush(self):
        return None

    def commit(self):
        return None

    def refresh(self, _value):
        return None


def test_enablement_host_and_device_walk_every_accessible_root(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    request = SimpleNamespace()
    monkeypatch.setattr(guidance, "request_device", lambda *_args: None)
    host = guidance.get_enablement(request, user, _Db(scalar=[0], scalars=[[]]))
    assert host.persona == "host_staff" and host.completed_count == 1

    device = SimpleNamespace(id="device-1")
    roots = [SimpleNamespace(id="denied"), SimpleNamespace(id="allowed")]
    monkeypatch.setattr(guidance, "request_device", lambda *_args: device)
    monkeypatch.setattr(
        guidance,
        "workspace_root_permissions",
        lambda _db, root, *_args: {"browse": root.id == "allowed"},
    )
    client = guidance.get_enablement(
        request,
        user,
        _Db(scalar=[1, 1, 0], scalars=[roots]),
    )
    assert client.persona == "client_staff"
    assert next(step for step in client.steps if step.key == "team_files").complete is True


def test_onboarding_existing_create_conflict_and_optional_patch_paths() -> None:
    user = SimpleNamespace(id="user-1")
    existing = OnboardingProgress(user_id=user.id, completed_steps=["profile"])
    existing.dismissed = False
    existing.version = 1
    existing.updated_at = datetime.now(UTC)
    result = guidance.get_onboarding(user, _Db(scalar=[existing]))
    assert result.completed_steps == ["profile"] and result.steps

    created_db = _Db(scalar=[None])
    created = guidance.get_onboarding(user, created_db)
    assert created.user_id == user.id and len(created_db.added) == 1

    with pytest.raises(ProblemException) as raised:
        guidance.patch_onboarding(
            OnboardingProgressPatch(), "2", user, _Db(scalar=[existing])
        )
    assert raised.value.code == "VERSION_CONFLICT"

    # 新建进度时同时过滤未知步骤并保存 dismissed。
    fresh_db = _Db(scalar=[None])
    patched = guidance.patch_onboarding(
        OnboardingProgressPatch(
            completed_steps=["profile", "unknown", ""], dismissed=True
        ),
        "1",
        user,
        fresh_db,
    )
    assert patched.completed_steps == ["profile"] and patched.dismissed is True

    # 两个可选字段都未提交时只推进并发版本，不清空既有状态。
    current = OnboardingProgress(
        user_id=user.id,
        completed_steps=["calendar"],
        dismissed=True,
        version=4,
    )
    current.updated_at = datetime.now(UTC)
    unchanged = guidance.patch_onboarding(
        OnboardingProgressPatch(), "4", user, _Db(scalar=[current])
    )
    assert unchanged.completed_steps == ["calendar"] and unchanged.dismissed is True
