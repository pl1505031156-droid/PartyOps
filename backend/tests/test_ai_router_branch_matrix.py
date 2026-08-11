"""本地智能管理、建议和草稿的权限与状态分支回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.enums import RecommendationStatus, UserRole
from app.models import AIDraft, AIModelPack, AIRecommendation
from app.problems import ProblemException
from app.routers import ai


class Rows:
    def __init__(self, values) -> None:
        self.values = values

    def all(self):
        return self.values


class Db:
    def __init__(self, objects=None, rows=None, scalars=None) -> None:
        self.objects = objects or {}
        self.rows = list(rows or [])
        self.scalar_values = list(scalars or [])

    def get(self, model, identity):
        return self.objects.get((model, identity), self.objects.get(identity))

    def scalars(self, _query):
        return Rows(self.rows.pop(0) if self.rows else [])

    def scalar(self, _query):
        return self.scalar_values.pop(0) if self.scalar_values else None

    def commit(self) -> None:
        return None

    def refresh(self, _item) -> None:
        return None


def request():
    return SimpleNamespace(client=None)


def user(role=UserRole.STAFF):
    return SimpleNamespace(id="user-1", role=role)


def assert_problem(code: str, call) -> None:
    with pytest.raises(ProblemException) as error:
        call()
    assert error.value.code == code


def test_parse_version_and_model_activation_guards(monkeypatch) -> None:
    assert ai.client_ip(request()) == ""
    assert ai.client_ip(SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))) == "127.0.0.1"
    assert_problem("IF_MATCH_REQUIRED", lambda: ai.parse_version(None))
    assert_problem("IF_MATCH_INVALID", lambda: ai.parse_version("bad"))
    assert ai.parse_version('"4"') == 4

    assert_problem(
        "MODEL_PACK_NOT_FOUND",
        lambda: ai.activate_local_model_pack("missing", request(), "embedding", user(UserRole.ADMIN), Db()),
    )
    pack = SimpleNamespace(id="p1", architecture="arm64")
    monkeypatch.setattr(ai, "normalized_architecture", lambda: "amd64")
    assert_problem(
        "MODEL_PACK_ARCH_MISMATCH",
        lambda: ai.activate_local_model_pack("p1", request(), "embedding", user(UserRole.ADMIN), Db(objects={(AIModelPack, "p1"): pack})),
    )


def test_model_capability_deactivation_routes_runtime(monkeypatch) -> None:
    admin = user(UserRole.ADMIN)
    assert_problem(
        "MODEL_CAPABILITY_INVALID",
        lambda: ai.deactivate_local_model_capability("vision", request(), admin, Db()),
    )
    calls = []
    monkeypatch.setattr(ai.llm_runtime, "stop", lambda: calls.append("llm"))
    monkeypatch.setattr(ai.embedding_runtime, "unload", lambda: calls.append("embedding"))
    monkeypatch.setattr(ai, "deactivate_model_capability", lambda _db, capability: None)
    monkeypatch.setattr(ai, "write_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "emit_event", lambda *_a, **_k: None)
    assert ai.deactivate_local_model_capability("llm", request(), admin, Db()) == {"capability": "llm", "active": False, "pack_id": None}
    assert ai.deactivate_local_model_capability("embedding", request(), admin, Db()) == {"capability": "embedding", "active": False, "pack_id": None}
    assert calls == ["llm", "embedding"]


def test_recommendation_owner_version_and_status_guards() -> None:
    staff = user()
    assert_problem(
        "AI_RECOMMENDATION_NOT_FOUND",
        lambda: ai._handle_recommendation("missing", RecommendationStatus.ACCEPTED, request(), '"1"', staff, Db()),
    )
    foreign = SimpleNamespace(id="r1", user_id="other", version=1, status=RecommendationStatus.PENDING)
    db = Db(objects={(AIRecommendation, "r1"): foreign})
    assert_problem(
        "AI_RECOMMENDATION_NOT_FOUND",
        lambda: ai._handle_recommendation("r1", RecommendationStatus.ACCEPTED, request(), '"1"', staff, db),
    )
    item = SimpleNamespace(id="r2", user_id="user-1", version=2, status=RecommendationStatus.PENDING)
    db = Db(objects={(AIRecommendation, "r2"): item})
    assert_problem(
        "VERSION_CONFLICT",
        lambda: ai._handle_recommendation("r2", RecommendationStatus.ACCEPTED, request(), '"1"', staff, db),
    )
    item.version = 1
    item.status = RecommendationStatus.ACCEPTED
    assert_problem(
        "AI_RECOMMENDATION_HANDLED",
        lambda: ai._handle_recommendation("r2", RecommendationStatus.ACCEPTED, request(), '"1"', staff, db),
    )


def test_ai_settings_test_requires_saved_provider() -> None:
    assert_problem(
        "AI_NOT_CONFIGURED",
        lambda: ai.test_ai_settings(request(), user(UserRole.ADMIN), Db(scalars=[None])),
    )
    assert_problem(
        "AI_NOT_CONFIGURED",
        lambda: ai.test_ai_settings(request(), user(UserRole.ADMIN), Db(scalars=[SimpleNamespace(base_url="")])),
    )


def test_draft_discard_and_approval_state_guards() -> None:
    staff = user()
    assert_problem("AI_DRAFT_NOT_FOUND", lambda: ai.discard_ai_draft("missing", request(), '"1"', staff, Db()))
    foreign = SimpleNamespace(id="d1", user_id="other", version=1, status="draft")
    db = Db(objects={(AIDraft, "d1"): foreign})
    assert_problem("AI_DRAFT_NOT_FOUND", lambda: ai.discard_ai_draft("d1", request(), '"1"', staff, db))
    own = SimpleNamespace(id="d2", user_id="user-1", version=2, status="draft")
    db = Db(objects={(AIDraft, "d2"): own})
    assert_problem("VERSION_CONFLICT", lambda: ai.discard_ai_draft("d2", request(), '"1"', staff, db))

    assert_problem("AI_DRAFT_NOT_FOUND", lambda: ai.approve_ai_draft("missing", request(), '"1"', staff, Db()))
    assert_problem("AI_DRAFT_NOT_FOUND", lambda: ai.approve_ai_draft("d1", request(), '"1"', staff, Db(objects={(AIDraft, "d1"): foreign})))
    own.version = 1
    own.status = "discarded"
    assert_problem("AI_DRAFT_ALREADY_HANDLED", lambda: ai.approve_ai_draft("d2", request(), '"1"', staff, db))


def test_approval_list_filters_staff_but_not_admin() -> None:
    draft = SimpleNamespace(id="d1")
    assert ai.list_ai_approvals(user(), Db(rows=[[draft]])) == [draft]
    assert ai.list_ai_approvals(user(UserRole.ADMIN), Db(rows=[[]])) == []
