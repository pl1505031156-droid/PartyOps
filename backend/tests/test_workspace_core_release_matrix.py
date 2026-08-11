"""原始文件中心路径边界、选择范围与受管固化回归。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import workspace
from app.enums import ContentIndexStatus, FileIndexStatus
from app.models import FileBlob
from app.problems import ProblemException


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return list(self.values)


def test_open_tokens_are_one_time_and_expired_tokens_are_pruned(monkeypatch) -> None:
    workspace._open_tokens.clear()
    workspace._open_tokens["expired"] = ("expired-file", workspace.time.monotonic() - 1)
    current = workspace.issue_local_open_token("current-file")
    assert "expired" not in workspace._open_tokens
    assert workspace.consume_local_open_token(current) == "current-file"
    assert workspace.consume_local_open_token(current) is None

    token = workspace.issue_local_open_token("file-1")
    assert workspace.consume_local_open_token(token) == "file-1"
    assert workspace.consume_local_open_token(token) is None


def test_validate_root_path_rejects_relative_missing_file_reserved_and_denied(monkeypatch, tmp_path) -> None:
    with pytest.raises(ProblemException) as relative:
        workspace.validate_root_path("relative/path")
    assert relative.value.code == "ROOT_PATH_NOT_ABSOLUTE"

    with pytest.raises(ProblemException) as missing:
        workspace.validate_root_path(str(tmp_path / "missing"))
    assert missing.value.code == "ROOT_PATH_UNAVAILABLE"

    file_path = tmp_path / "not-directory.txt"
    file_path.write_text("file", encoding="utf-8")
    with pytest.raises(ProblemException) as not_directory:
        workspace.validate_root_path(str(file_path))
    assert not_directory.value.code == "ROOT_PATH_NOT_DIRECTORY"

    data_dir = tmp_path / "system-data"
    data_dir.mkdir()
    monkeypatch.setattr(workspace, "get_settings", lambda: SimpleNamespace(data_dir=data_dir))
    with pytest.raises(ProblemException) as reserved:
        workspace.validate_root_path(str(data_dir))
    assert reserved.value.code == "ROOT_PATH_RESERVED"

    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(workspace.os, "access", lambda *_args: False)
    with pytest.raises(ProblemException) as denied:
        workspace.validate_root_path(str(shared))
    assert denied.value.code == "ROOT_PATH_PERMISSION_DENIED"


def test_resolve_workspace_path_prevents_missing_and_escape(tmp_path) -> None:
    root_dir = tmp_path / "root"
    root_dir.mkdir()
    root = SimpleNamespace(absolute_path=str(root_dir))
    with pytest.raises(ProblemException) as missing:
        workspace.resolve_workspace_path(root, "missing.txt")
    assert missing.value.code == "WORKSPACE_FILE_MISSING"

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(ProblemException) as escaped:
        workspace.resolve_workspace_path(root, "../outside.txt")
    assert escaped.value.code == "WORKSPACE_PATH_OUTSIDE_ROOT"

    allowed = root_dir / "allowed.txt"
    allowed.write_text("ok", encoding="utf-8")
    assert workspace.resolve_workspace_path(root, "allowed.txt") == allowed.resolve()


def test_selection_normalization_scope_and_discovered_validation() -> None:
    assert workspace.normalize_included_paths([".", "docs"]) == ["."]
    assert workspace.normalize_included_paths(["docs", "docs/reports", "images"]) == ["docs", "images"]
    for unsafe in ("../outside", "/absolute", "x" * 2_049):
        with pytest.raises(ProblemException):
            workspace.normalize_included_paths([unsafe])

    assert workspace.path_scope_state("anything.txt", is_directory=False, selection_mode="all", included_paths=[])
    assert not workspace.path_scope_state("anything.txt", is_directory=False, selection_mode="selected", included_paths=[])
    assert workspace.path_scope_state("docs", is_directory=True, selection_mode="selected", included_paths=["docs/reports"])
    assert workspace.path_scope_state("docs/reports/a.pdf", is_directory=False, selection_mode="selected", included_paths=["docs"])
    assert not workspace.path_scope_state("private/a.pdf", is_directory=False, selection_mode="selected", included_paths=["docs"])

    db = SimpleNamespace(scalars=lambda _statement: _Rows(["docs", "images"]))
    root = SimpleNamespace(id="root-1")
    assert workspace.validate_selection_paths(db, root, ["."]) == ["."]
    assert workspace.validate_selection_paths(db, root, []) == []
    assert workspace.validate_selection_paths(db, root, ["docs"]) == ["docs"]
    with pytest.raises(ProblemException) as unknown:
        workspace.validate_selection_paths(db, root, ["missing"])
    assert unknown.value.code == "WORKSPACE_SELECTION_NOT_DISCOVERED"
    assert unknown.value.extra["paths"] == ["missing"]


class _NodeDb:
    def __init__(self):
        self.added = []
        self.flushes = 0

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushes += 1


def _indexed_item(path: Path):
    stat = path.stat()
    return SimpleNamespace(
        id="file-1",
        parent_id=None,
        relative_path=path.name,
        name=path.name,
        is_directory=False,
        in_scope=True,
        extension=path.suffix,
        size_bytes=stat.st_size,
        modified_at=None,
        device_id="",
        inode="",
        mime_type="text/plain",
        detected_type="text/plain",
        content_status=ContentIndexStatus.METADATA_ONLY,
        content_error_code="",
        archive_member_count=0,
        last_seen_at=None,
        indexed_at=None,
        status=FileIndexStatus.INDEXED,
        version=2,
        sha256="old",
        extracted_text="old",
        ocr_text="old",
    )


def test_upsert_node_extracts_opted_in_utf8_and_gb18030(tmp_path) -> None:
    db = _NodeDb()
    root = SimpleNamespace(id="root-1")
    seen_at = workspace.utcnow()
    utf8 = tmp_path / "notice.txt"
    utf8.write_text("共享正文", encoding="utf-8")
    item = _indexed_item(utf8)
    result, changed = workspace._upsert_node(
        db, root, {utf8.name: item}, None, utf8.name, utf8, False, seen_at,
        extract_content=True, in_scope=True,
    )
    assert changed is True and result.extracted_text == "共享正文"
    assert result.content_status == ContentIndexStatus.INDEXED

    gb = tmp_path / "legacy.txt"
    gb.write_bytes("国产环境正文".encode("gb18030"))
    item = _indexed_item(gb)
    result, _ = workspace._upsert_node(
        db, root, {gb.name: item}, None, gb.name, gb, False, seen_at,
        extract_content=True, in_scope=True,
    )
    assert result.extracted_text == "国产环境正文"


class _BlobDb:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flushes = 0

    def get(self, model, _key):
        assert model is FileBlob
        return self.existing

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flushes += 1


def test_store_managed_path_checks_source_hash_copy_and_dedup(monkeypatch, tmp_path) -> None:
    attachments = tmp_path / "attachments"
    monkeypatch.setattr(workspace, "get_settings", lambda: SimpleNamespace(attachments_dir=attachments))
    source = tmp_path / "source.txt"
    source.write_bytes(b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()

    with pytest.raises(ProblemException) as missing:
        workspace.store_managed_path(_BlobDb(), tmp_path / "missing", "missing", "text/plain")
    assert missing.value.code == "SOURCE_MISSING"
    with pytest.raises(ProblemException) as mismatch:
        workspace.store_managed_path(_BlobDb(), source, source.name, "text/plain", "0" * 64)
    assert mismatch.value.code == "HASH_MISMATCH"

    db = _BlobDb()
    blob = workspace.store_managed_path(db, source, source.name, "text/plain", digest)
    assert blob.sha256 == digest and len(db.added) == 1
    stored = attachments / blob.relative_path
    assert stored.read_bytes() == b"verified"

    existing = SimpleNamespace(sha256=digest)
    assert workspace.store_managed_path(_BlobDb(existing), source, source.name, "text/plain") is existing

    # 模拟复制后介质内容变化，必须删除临时文件并拒绝固化。
    stored.unlink()
    hashes = iter([digest, "different"])
    monkeypatch.setattr(workspace, "hash_file", lambda _path: next(hashes))
    with pytest.raises(ProblemException) as copy_mismatch:
        workspace.store_managed_path(_BlobDb(), source, source.name, "text/plain")
    assert copy_mismatch.value.code == "FREEZE_HASH_MISMATCH"
    assert not stored.with_suffix(".incoming").exists()


def test_search_workspace_files_empty_fts_hit_and_fallback() -> None:
    expected = [SimpleNamespace(id="file-1")]

    class _SearchDb:
        def __init__(self, ids):
            self.ids = ids
            self.execute_calls = 0

        def execute(self, _statement, _params):
            self.execute_calls += 1
            return _Rows([(value,) for value in self.ids])

        def scalars(self, _statement):
            return _Rows(expected)

    empty = _SearchDb([])
    assert workspace.search_workspace_files(empty, "", root_id="root-1") == expected
    assert empty.execute_calls == 0
    hit = _SearchDb(["file-1"])
    assert workspace.search_workspace_files(hit, 'annual "report"') == expected
    fallback = _SearchDb([])
    assert workspace.search_workspace_files(fallback, "not-found") == expected
