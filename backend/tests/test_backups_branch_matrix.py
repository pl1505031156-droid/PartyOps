"""备份版本识别、解压边界和清单异常分支回归。"""

from __future__ import annotations

import json
import stat
import zipfile
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import backups
from app.problems import ProblemException


class SqlResult:
    def __init__(self, *, rows=None, scalar_value=None) -> None:
        self.rows = rows or []
        self.scalar_value = scalar_value

    def fetchall(self):
        return self.rows

    def scalar(self):
        return self.scalar_value


class Connection:
    def __init__(self, tables, values=None, fail=False) -> None:
        self.tables = tables
        self.values = list(values or [])
        self.fail = fail

    def __enter__(self):
        if self.fail:
            raise RuntimeError("database unavailable")
        return self

    def __exit__(self, *_args):
        return None

    def exec_driver_sql(self, statement):
        if "sqlite_master" in statement:
            return SqlResult(rows=[(name,) for name in self.tables])
        return SqlResult(scalar_value=self.values.pop(0) if self.values else None)


class Engine:
    def __init__(self, connection) -> None:
        self.connection = connection

    def connect(self):
        return self.connection


def assert_problem(code: str, call) -> None:
    with pytest.raises(ProblemException) as error:
        call()
    assert error.value.code == code


@pytest.mark.parametrize(
    ("tables", "values", "expected"),
    [
        ({"alembic_version"}, ["0018"], "0018"),
        ({"alembic_version", "schema_release_notes"}, [None, "0017"], "0017"),
        ({"schema_release_notes", "tasks"}, [None], "0002"),
        (set(), [], "0001"),
    ],
)
def test_current_schema_version_fallback_chain(monkeypatch, tables, values, expected) -> None:
    monkeypatch.setattr(backups.db_runtime, "engine", Engine(Connection(tables, values)))
    assert backups.current_schema_version() == expected
    monkeypatch.setattr(backups.db_runtime, "engine", Engine(Connection(set(), fail=True)))
    assert backups.current_schema_version() == "0001"


def info(name: str, size: int = 0, compressed: int = 0, mode: int = 0) -> zipfile.ZipInfo:
    item = zipfile.ZipInfo(name)
    item.file_size = size
    item.compress_size = compressed
    item.external_attr = mode << 16
    return item


class Archive:
    def __init__(self, members) -> None:
        self.members = members

    def infolist(self):
        return self.members


def test_zip_member_limits_paths_and_bomb_guards(monkeypatch) -> None:
    settings = SimpleNamespace(backup_max_members=1, backup_restore_max_gb=1)
    monkeypatch.setattr(backups, "get_settings", lambda: settings)
    assert_problem("BACKUP_MEMBER_LIMIT", lambda: backups._validated_zip_infos(Archive([info("a"), info("b")])))

    settings.backup_max_members = 10
    for member in (info(""), info("/absolute"), info("../escape"), info("link", mode=stat.S_IFLNK)):
        assert_problem("BACKUP_PATH_INVALID", lambda member=member: backups._validated_zip_infos(Archive([member])))
    assert_problem("BACKUP_PATH_INVALID", lambda: backups._validated_zip_infos(Archive([info("same"), info("same")])))
    assert_problem("BACKUP_PATH_INVALID", lambda: backups._validated_zip_infos(Archive([info("Folder/a"), info("folder/A")])))
    for name in ("folder//file", "file:stream", "con.txt", "folder/name. "):
        assert_problem("BACKUP_PATH_INVALID", lambda name=name: backups._validated_zip_infos(Archive([info(name)])))
    encrypted = info("encrypted")
    encrypted.flag_bits = 1
    assert_problem("BACKUP_PATH_INVALID", lambda: backups._validated_zip_infos(Archive([encrypted])))
    unsupported = info("unsupported")
    unsupported.compress_type = zipfile.ZIP_BZIP2
    assert_problem("BACKUP_PATH_INVALID", lambda: backups._validated_zip_infos(Archive([unsupported])))

    settings.backup_restore_max_gb = 0
    assert_problem("BACKUP_EXPANDED_LIMIT", lambda: backups._validated_zip_infos(Archive([info("large", 1, 1)])))
    settings.backup_restore_max_gb = 1
    huge = 101 * 1024**2
    assert_problem("BACKUP_COMPRESSION_INVALID", lambda: backups._validated_zip_infos(Archive([info("bomb", huge, 1)])))
    assert backups._validated_zip_infos(Archive([info("safe", 32, 16)]))[0].filename == "safe"


def test_safe_extract_directory_file_and_data_child(monkeypatch, tmp_path: Path) -> None:
    archive_path = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("folder/", b"")
        archive.writestr("folder/value.txt", b"ok")
    destination = tmp_path / "destination"
    with zipfile.ZipFile(archive_path) as archive:
        backups._safe_zip_members(archive, destination)
    assert (destination / "folder").is_dir()
    assert (destination / "folder" / "value.txt").read_bytes() == b"ok"

    data_dir = tmp_path / "data"
    monkeypatch.setattr(backups, "get_settings", lambda: SimpleNamespace(data_dir=data_dir))
    assert backups._ensure_data_child(data_dir / "child") == (data_dir / "child").resolve()
    with pytest.raises(RuntimeError):
        backups._ensure_data_child(data_dir)
    with pytest.raises(RuntimeError):
        backups._ensure_data_child(tmp_path / "outside")


def write_manifest_archive(path: Path, files) -> None:
    database = b"not-used-for-this-guard"
    manifest = {
        "format": "partyops-backup",
        "format_version": 1,
        "schema_version": "0018",
        "files": files,
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("database/partyops.db", database)
        archive.writestr("manifest.json", json.dumps(manifest))


def test_verify_manifest_rejects_non_object_and_bad_hash_fields(tmp_path: Path) -> None:
    invalid_item = tmp_path / "invalid-item.zip"
    write_manifest_archive(invalid_item, ["database/partyops.db"])
    assert_problem("BACKUP_MANIFEST_INVALID", lambda: backups.verify_backup(invalid_item))

    invalid_fields = tmp_path / "invalid-fields.zip"
    write_manifest_archive(invalid_fields, [{"path": "database/partyops.db", "size": "bad"}])
    assert_problem("BACKUP_MANIFEST_INVALID", lambda: backups.verify_backup(invalid_fields))

    wrong_hash = tmp_path / "wrong-hash.zip"
    write_manifest_archive(
        wrong_hash,
        [{"path": "database/partyops.db", "size": 21, "sha256": sha256(b"different").hexdigest()}],
    )
    assert_problem("BACKUP_HASH_MISMATCH", lambda: backups.verify_backup(wrong_hash))


def test_verify_manifest_is_bounded_and_has_stable_version_errors(
    monkeypatch, tmp_path: Path
) -> None:
    oversized = tmp_path / "oversized-manifest.zip"
    with zipfile.ZipFile(oversized, "w") as archive:
        archive.writestr("manifest.json", "{}")
    monkeypatch.setattr(backups, "MAX_BACKUP_MANIFEST_BYTES", 1)
    assert_problem("BACKUP_MANIFEST_INVALID", lambda: backups.verify_backup(oversized))

    monkeypatch.setattr(backups, "MAX_BACKUP_MANIFEST_BYTES", 1024 * 1024)
    for name, manifest in (
        (
            "invalid-version.zip",
            {"format": "partyops-backup", "format_version": "bad", "files": []},
        ),
        (
            "zero-version.zip",
            {"format": "partyops-backup", "format_version": 0, "files": []},
        ),
    ):
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
        assert_problem("BACKUP_MANIFEST_INVALID", lambda path=path: backups.verify_backup(path))

    invalid_utf8 = tmp_path / "invalid-utf8.zip"
    with zipfile.ZipFile(invalid_utf8, "w") as archive:
        archive.writestr("manifest.json", b"\xff\xfe")
    assert_problem("BACKUP_MANIFEST_INVALID", lambda: backups.verify_backup(invalid_utf8))


def test_verify_backup_translates_payload_crc_damage(tmp_path: Path) -> None:
    payload = b"PARTYOPS_DB_PAYLOAD_UNIQUE"
    path = tmp_path / "crc-damaged.zip"
    manifest = {
        "format": "partyops-backup",
        "format_version": 1,
        "schema_version": "0019",
        "files": [
            {
                "path": "database/partyops.db",
                "size": len(payload),
                "sha256": sha256(payload).hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("database/partyops.db", payload)
    raw = path.read_bytes()
    assert raw.count(payload) == 1
    path.write_bytes(raw.replace(payload, b"X" + payload[1:], 1))
    assert_problem("BACKUP_HASH_MISMATCH", lambda: backups.verify_backup(path))
