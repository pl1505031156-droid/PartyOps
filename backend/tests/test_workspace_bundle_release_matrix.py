"""跨机文件批量 ZIP 的路径、容量和失败清理回归。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.problems import ProblemException
from app.routers import fleet


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class _BundleDb:
    def __init__(self, children=None):
        self.children = children or []

    def scalars(self, _statement):
        return _Rows(self.children)


def _item(item_id: str, relative: str, *, directory=False, size=0):
    return SimpleNamespace(
        id=item_id,
        root_id="root-1",
        relative_path=relative,
        name=Path(relative).name,
        is_directory=directory,
        in_scope=True,
        status="indexed",
        size_bytes=size,
        sha256="",
    )


def test_workspace_download_item_validation(monkeypatch) -> None:
    user = SimpleNamespace(id="user-1")
    settings = SimpleNamespace(transfer_max_file_gb=20)
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)
    with pytest.raises(ProblemException) as duplicate:
        fleet._workspace_download_items(SimpleNamespace(), ["one", "one"], user, None)
    assert duplicate.value.code == "DUPLICATE_DOWNLOAD_ITEM"

    missing_db = SimpleNamespace(get=lambda *_args: None)
    with pytest.raises(ProblemException) as missing:
        fleet._workspace_download_items(missing_db, ["missing"], user, None)
    assert missing.value.code == "WORKSPACE_FILE_NOT_FOUND"

    item = _item("file-1", "file.txt", size=10)
    root = SimpleNamespace(id="root-1")

    class _Db:
        def get(self, model, _id):
            return item if model.__name__ == "WorkspaceFile" else root

    monkeypatch.setattr(fleet, "workspace_root_permissions", lambda *_args: {"download": False})
    with pytest.raises(ProblemException) as denied:
        fleet._workspace_download_items(_Db(), ["file-1"], user, None)
    assert denied.value.code == "WORKSPACE_ACCESS_DENIED"

    monkeypatch.setattr(fleet, "workspace_root_permissions", lambda *_args: {"download": True})
    settings.transfer_max_file_gb = 0
    with pytest.raises(ProblemException) as large:
        fleet._workspace_download_items(_Db(), ["file-1"], user, None)
    assert large.value.code == "TRANSFER_FILE_TOO_LARGE"


def test_host_bundle_builds_directory_zip_deduplicates_and_hashes(monkeypatch, tmp_path) -> None:
    root_path = tmp_path / "shared"
    docs = root_path / "docs"
    docs.mkdir(parents=True)
    report = docs / "report.txt"
    report.write_text("年度报告", encoding="utf-8")
    root = SimpleNamespace(id="root-1", name="主机资料", absolute_path=str(root_path))
    directory = _item("dir-1", "docs", directory=True)
    child = _item("file-1", "docs/report.txt", size=report.stat().st_size)
    transfer = SimpleNamespace(id="transfer-1")
    transfers = tmp_path / "transfers"
    settings = SimpleNamespace(transfer_max_file_gb=20, transfers_dir=transfers)
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)

    part = fleet._host_bundle_to_transit(
        _BundleDb([child]), transfer, [(directory, root), (child, root)]
    )
    assert part.is_file()
    with zipfile.ZipFile(part) as archive:
        assert archive.namelist().count("主机资料/docs/report.txt") == 1
        assert archive.read("主机资料/docs/report.txt").decode("utf-8") == "年度报告"


def test_host_bundle_rejects_invalid_index_capacity_and_cleans_partial(monkeypatch, tmp_path) -> None:
    root_path = tmp_path / "root"
    root_path.mkdir()
    source = root_path / "file.txt"
    source.write_bytes(b"content")
    root = SimpleNamespace(id="root-1", name="根目录", absolute_path=str(root_path))
    item = _item("file-1", "file.txt", size=source.stat().st_size)
    transfer = SimpleNamespace(id="transfer-2")
    settings = SimpleNamespace(transfer_max_file_gb=0, transfers_dir=tmp_path / "transfers")
    monkeypatch.setattr(fleet, "get_settings", lambda: settings)
    with pytest.raises(ProblemException) as capacity:
        fleet._host_bundle_to_transit(_BundleDb(), transfer, [(item, root)])
    assert capacity.value.code == "TRANSFER_FILE_TOO_LARGE"

    settings.transfer_max_file_gb = 20
    invalid = _item("invalid", "../file.txt", size=1)
    monkeypatch.setattr(fleet, "resolve_workspace_path", lambda *_args: source)
    with pytest.raises(ProblemException) as path_error:
        fleet._host_bundle_to_transit(_BundleDb(), transfer, [(invalid, root)])
    assert path_error.value.code == "WORKSPACE_PATH_INVALID"

    monkeypatch.setattr(fleet, "resolve_workspace_path", lambda *_args: source)
    real_zip = fleet.zipfile.ZipFile
    monkeypatch.setattr(
        fleet.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk failure")),
    )
    with pytest.raises(OSError):
        fleet._host_bundle_to_transit(_BundleDb(), transfer, [(item, root)])
    assert not (settings.transfers_dir / "transfer-2.part").exists()
    monkeypatch.setattr(fleet.zipfile, "ZipFile", real_zip)
