"""本地模型包与 AI 草稿生命周期剩余分支回归。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.problems import ProblemException
from app.routers import ai as ai_routes
from app.schemas import AIQueryRequest


class _Rows:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return self.values


class _Upload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self.chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class _Request:
    client = SimpleNamespace(host="127.0.0.1")


def _code(call, expected: str) -> None:
    with pytest.raises(ProblemException) as raised:
        call()
    assert raised.value.code == expected


def test_model_pack_list_upload_limit_and_commit_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activation = SimpleNamespace(model_pack_id="pack-1", capability="intent_router")
    pack = SimpleNamespace(id="pack-1")

    class Output:
        def __init__(self, source):
            self.source = source
            self.active_capabilities: list[str] = []

        @classmethod
        def model_validate(cls, source):
            return cls(source)

        def model_copy(self, *, update):
            self.active_capabilities = update["active_capabilities"]
            return self

    class ListDb:
        def __init__(self):
            self.calls = 0

        def scalars(self, _statement):
            self.calls += 1
            return _Rows([activation] if self.calls == 1 else [pack])

    cleaned: list[bool] = []
    monkeypatch.setattr(ai_routes, "AIModelPackOut", Output)
    monkeypatch.setattr(
        ai_routes,
        "cleanup_model_pack_uninstall_staging",
        lambda: cleaned.append(True),
    )
    listed = ai_routes.list_model_packs(SimpleNamespace(), ListDb())
    assert cleaned == [True]
    assert listed[0].active_capabilities == ["intent_router"]

    settings = SimpleNamespace(models_dir=tmp_path / "models")
    monkeypatch.setattr(ai_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_routes, "MAX_MODEL_UPLOAD_BYTES", 1)

    class UploadDb:
        def __init__(self, *, fail_commit=False):
            self.fail_commit = fail_commit
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

        def commit(self):
            if self.fail_commit:
                raise RuntimeError("database commit failed")

        def refresh(self, _value):
            return None

    limited_db = UploadDb()
    with pytest.raises(ProblemException) as too_large:
        asyncio.run(
            ai_routes.upload_model_pack(
                _Request(),
                _Upload("needle.partyops-modelpack", [b"xx"]),
                SimpleNamespace(id="admin"),
                limited_db,
            )
        )
    assert too_large.value.code == "MODEL_PACK_TOO_LARGE"
    assert limited_db.rollbacks == 1
    assert not list(settings.models_dir.rglob("upload-*"))

    monkeypatch.setattr(ai_routes, "MAX_MODEL_UPLOAD_BYTES", 1024)
    installed = SimpleNamespace(
        id="pack-2",
        model_id="needle-2",
        version="2.0",
        signature_valid=True,
    )
    monkeypatch.setattr(ai_routes, "install_model_pack", lambda *_args: installed)
    monkeypatch.setattr(ai_routes, "write_audit", lambda *_args, **_kwargs: None)
    removed: list[object] = []
    monkeypatch.setattr(
        ai_routes, "remove_installed_pack_files", lambda value: removed.append(value)
    )
    commit_db = UploadDb(fail_commit=True)
    with pytest.raises(RuntimeError, match="commit failed"):
        asyncio.run(
            ai_routes.upload_model_pack(
                _Request(),
                _Upload("needle.partyops-modelpack", [b"valid"]),
                SimpleNamespace(id="admin"),
                commit_db,
            )
        )
    assert removed == [installed] and commit_db.rollbacks == 1


def test_model_pack_probe_busy_files_and_database_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = SimpleNamespace(
        id="pack-1",
        architecture="universal",
        model_id="needle-2",
        version="2.0",
    )
    request = _Request()
    admin = SimpleNamespace(id="admin")

    class Db:
        def __init__(self, active=None, fail_commit=False):
            self.active = active or []
            self.fail_commit = fail_commit
            self.rollbacks = 0
            self.deleted: list[object] = []

        def get(self, _model, _identifier):
            return pack

        def scalars(self, _statement):
            return _Rows(self.active)

        def delete(self, value):
            self.deleted.append(value)

        def commit(self):
            if self.fail_commit:
                raise RuntimeError("database unavailable")

        def rollback(self):
            self.rollbacks += 1

    monkeypatch.setattr(
        ai_routes.needle_intent_runtime,
        "probe",
        lambda _pack: (_ for _ in ()).throw(OSError("native runtime denied")),
    )
    _code(
        lambda: ai_routes.activate_local_model_pack(
            "pack-1", request, "intent_router", admin, Db()
        ),
        "NEEDLE_RUNTIME_INVALID",
    )

    monkeypatch.setattr(
        ai_routes,
        "stage_model_pack_removal",
        lambda _pack: (_ for _ in ()).throw(OSError("file busy")),
    )
    _code(
        lambda: ai_routes.uninstall_local_model_pack(
            "pack-1", request, admin, Db()
        ),
        "MODEL_PACK_FILES_BUSY",
    )

    stage = tmp_path / "stage"
    moves = [(tmp_path / "installed", stage / "installed")]
    monkeypatch.setattr(
        ai_routes, "stage_model_pack_removal", lambda _pack: (stage, moves)
    )
    monkeypatch.setattr(ai_routes, "write_audit", lambda *_args, **_kwargs: None)
    rolled_back: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        ai_routes,
        "rollback_staged_model_pack_removal",
        lambda root, values: rolled_back.append((root, values)),
    )
    db = Db(fail_commit=True)
    with pytest.raises(RuntimeError, match="database unavailable"):
        ai_routes.uninstall_local_model_pack("pack-1", request, admin, db)
    assert db.deleted == [pack] and db.rollbacks == 1
    assert rolled_back == [(stage, moves)]


class _QueryDb:
    def __init__(self, policy):
        self.scalar_values = [None, policy]
        self.objects: dict[tuple[type, str], object] = {}
        self.added: list[object] = []
        self.commits = 0

    def scalar(self, _statement):
        return self.scalar_values.pop(0)

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = f"object-{len(self.added) + 1}"
        self.added.append(value)
        self.objects[(type(value), value.id)] = value

    def get(self, model, identifier):
        return self.objects[(model, identifier)]

    def commit(self):
        self.commits += 1

    def flush(self):
        return None

    def refresh(self, _value):
        return None


@pytest.mark.parametrize("succeed", [False, True])
def test_local_ai_query_records_failure_or_completed_draft(
    monkeypatch: pytest.MonkeyPatch,
    succeed: bool,
) -> None:
    policy = SimpleNamespace(active=True, capabilities=["summarize"])
    db = _QueryDb(policy)
    user = SimpleNamespace(id="user-1", role=SimpleNamespace(value="admin"))
    payload = AIQueryRequest(
        capability="summarize",
        instruction="概括会议材料",
        task_ids=["task-1"],
    )
    monkeypatch.setattr(
        ai_routes,
        "collect_sources",
        lambda *_args, **_kwargs: (
            [{"id": "task-1", "type": "task"}],
            ["会议材料正文"],
        ),
    )
    if succeed:
        monkeypatch.setattr(
            ai_routes, "complete_locally", lambda *_args: "本地离线摘要"
        )
        monkeypatch.setattr(ai_routes, "write_audit", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(ai_routes, "emit_event", lambda *_args, **_kwargs: None)
        draft = ai_routes.query_ai(payload, _Request(), user, db)
        assert draft.content == "本地离线摘要"
        assert draft.sources == [{"id": "task-1", "type": "task"}]
        invocation = db.added[0]
        assert invocation.status == "completed" and invocation.completed_at is not None
    else:
        monkeypatch.setattr(
            ai_routes,
            "complete_locally",
            lambda *_args: (_ for _ in ()).throw(
                ProblemException(503, "LOCAL_AI_FAILED", "本地推理失败", "请重试。")
            ),
        )
        with pytest.raises(ProblemException) as failed:
            ai_routes.query_ai(payload, _Request(), user, db)
        assert failed.value.code == "LOCAL_AI_FAILED"
        invocation = db.added[0]
        assert invocation.status == "failed"
        assert invocation.error_code == "LOCAL_AI_FAILED"
