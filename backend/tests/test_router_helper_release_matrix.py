"""文件中心与更新路由的边界契约回归。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import UserRole
from app.models import WorkspaceFile, WorkspaceRoot
from app.problems import ProblemException
from app.routers import updates
from app.routers import workspace as workspace_router


class _Db:
    def __init__(self, objects=None):
        self.objects = objects or {}

    def get(self, model, object_id):
        return self.objects.get((model, object_id))


def test_workspace_route_helpers_reject_bad_versions_and_permissions(monkeypatch) -> None:
    assert workspace_router.parse_version('"12"') == 12
    with pytest.raises(ProblemException) as missing:
        workspace_router.parse_version(None)
    assert missing.value.code == "IF_MATCH_REQUIRED"
    with pytest.raises(ProblemException) as invalid:
        workspace_router.parse_version("not-number")
    assert invalid.value.code == "IF_MATCH_INVALID"

    user = SimpleNamespace(id="user-1", role=UserRole.STAFF)
    item = SimpleNamespace(id="file-1", root_id="root-1", in_scope=True)
    root = SimpleNamespace(id="root-1", enabled=True)
    db = _Db({(WorkspaceFile, "file-1"): item, (WorkspaceRoot, "root-1"): root})
    monkeypatch.setattr(
        workspace_router,
        "workspace_root_permissions",
        lambda *_args: {"browse": True, "download": False, "manage_root": False},
    )
    returned_item, returned_root = workspace_router.get_file(db, "file-1", user, None)
    assert returned_item is item and returned_root is root
    with pytest.raises(ProblemException) as denied:
        workspace_router.get_file(db, "file-1", user, None, "download")
    assert denied.value.code == "WORKSPACE_ACCESS_DENIED"
    with pytest.raises(ProblemException) as missing_file:
        workspace_router.get_file(db, "missing", user, None)
    assert missing_file.value.code == "WORKSPACE_FILE_NOT_FOUND"

    root.enabled = False
    with pytest.raises(ProblemException) as disabled:
        workspace_router.get_file(db, "file-1", user, None)
    assert disabled.value.code == "WORKSPACE_ROOT_DISABLED"
    root.enabled = True
    item.in_scope = False
    with pytest.raises(ProblemException) as out_of_scope:
        workspace_router.get_file(db, "file-1", user, None)
    assert out_of_scope.value.code == "WORKSPACE_FILE_OUT_OF_SCOPE"

    with pytest.raises(ProblemException) as root_missing:
        workspace_router.require_root_manager(db, "missing", user, None)
    assert root_missing.value.code == "WORKSPACE_ROOT_NOT_FOUND"
    with pytest.raises(ProblemException) as manage_denied:
        workspace_router.require_root_manager(db, "root-1", user, None)
    assert manage_denied.value.code == "WORKSPACE_ROOT_MANAGE_DENIED"


def test_host_local_detection_handles_loopback_invalid_dns_and_local_address(monkeypatch) -> None:
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    assert workspace_router.is_host_local_request(request) is True
    request.client.host = "not-an-ip"
    assert workspace_router.is_host_local_request(request) is False

    request.client.host = "192.168.10.8"
    settings = SimpleNamespace(environment="production", host="192.168.10.8")
    monkeypatch.setattr(workspace_router, "get_settings", lambda: settings)
    monkeypatch.setattr(
        workspace_router.socket,
        "getaddrinfo",
        lambda *_args: [(None, None, None, None, ("192.168.10.8", 0))],
    )
    assert workspace_router.is_host_local_request(request) is True
    monkeypatch.setattr(
        workspace_router.socket,
        "getaddrinfo",
        lambda *_args: (_ for _ in ()).throw(OSError("dns unavailable")),
    )
    request.client.host = "192.168.10.9"
    assert workspace_router.is_host_local_request(request) is False


def _valid_update_manifest() -> dict:
    artifacts = {
        "partyops_9.9.9_amd64.deb": {"size": 1, "sha256": "0" * 64},
        "partyops_9.9.9_arm64.deb": {"size": 1, "sha256": "0" * 64},
        "PartyOps_9.9.9_windows_amd64.exe": {"size": 1, "sha256": "0" * 64},
    }
    architecture = {
        "amd64": "partyops_9.9.9_amd64.deb",
        "arm64": "partyops_9.9.9_arm64.deb",
    }
    return {
        "format": "partyops-update",
        "format_version": 2,
        "version": "9.9.9",
        "min_version": "1.3.4",
        "schema_revision": "0018",
        "release_notes": ["正式发布回归"],
        "architecture_artifacts": architecture,
        "platform_artifacts": {
            "uos": architecture,
            "windows": {"amd64": "PartyOps_9.9.9_windows_amd64.exe"},
        },
        "artifacts": artifacts,
    }


def test_update_contract_rejects_incomplete_platform_version_schema_and_notes(monkeypatch) -> None:
    monkeypatch.setattr(updates, "__version__", "1.4.2")
    base = _valid_update_manifest()
    updates._validate_manifest_contract(base)
    cases = [
        ({**base, "format": "zip"}, "UPDATE_FORMAT_INVALID"),
        ({**base, "format_version": 3}, "UPDATE_FORMAT_VERSION_UNSUPPORTED"),
        ({**base, "architecture_artifacts": {"amd64": "x"}}, "UPDATE_ARCHITECTURES_INCOMPLETE"),
        ({**base, "artifacts": []}, "UPDATE_MANIFEST_INVALID"),
        ({**base, "architecture_artifacts": {"amd64": "wrong.exe", "arm64": "wrong.exe"}}, "UPDATE_ARCHITECTURE_ARTIFACT_INVALID"),
        ({**base, "platform_artifacts": []}, "UPDATE_PLATFORM_ARTIFACTS_INVALID"),
        ({**base, "platform_artifacts": {"uos": {}, "windows": {"amd64": "x"}}}, "UPDATE_UOS_ARTIFACTS_INVALID"),
        ({**base, "platform_artifacts": {"uos": base["architecture_artifacts"], "windows": {}}}, "UPDATE_WINDOWS_ARTIFACT_MISSING"),
        ({**base, "platform_artifacts": {"uos": base["architecture_artifacts"], "windows": {"amd64": "wrong.exe"}}}, "UPDATE_WINDOWS_ARTIFACT_INVALID"),
        ({**base, "version": "1.4.2"}, "UPDATE_VERSION_NOT_NEWER"),
        ({**base, "min_version": "9.0.0"}, "UPDATE_BRIDGE_REQUIRED"),
        ({**base, "schema_revision": "17"}, "UPDATE_SCHEMA_INVALID"),
        ({**base, "schema_revision": "0001"}, "UPDATE_SCHEMA_DOWNGRADE"),
        ({**base, "release_notes": []}, "UPDATE_RELEASE_NOTES_INVALID"),
    ]
    for manifest, code in cases:
        with pytest.raises(ProblemException) as error:
            updates._validate_manifest_contract(manifest)
        assert error.value.code == code
    with pytest.raises(ProblemException):
        updates._version_tuple("1.4")


def test_update_manifest_archive_guards_and_hash_validation(monkeypatch, tmp_path) -> None:
    with pytest.raises(ProblemException):
        updates._safe_zip_member("../escape")
    monkeypatch.setattr(updates, "_validate_manifest_contract", lambda _manifest: None)

    invalid_zip = tmp_path / "invalid.partyops-update"
    invalid_zip.write_bytes(b"invalid")
    with pytest.raises(ProblemException) as invalid:
        updates._extract_manifest(invalid_zip)
    assert invalid.value.code == "UPDATE_PACKAGE_INVALID"

    missing = tmp_path / "missing.partyops-update"
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("readme.txt", "missing manifest")
    with pytest.raises(ProblemException) as no_manifest:
        updates._extract_manifest(missing)
    assert no_manifest.value.code == "UPDATE_MANIFEST_MISSING"

    invalid_json = tmp_path / "json.partyops-update"
    with zipfile.ZipFile(invalid_json, "w") as archive:
        archive.writestr("manifest.json", "not-json")
    with pytest.raises(ProblemException) as bad_json:
        updates._extract_manifest(invalid_json)
    assert bad_json.value.code == "UPDATE_MANIFEST_INVALID"

    incomplete = tmp_path / "incomplete.partyops-update"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"version": "9.9.9"}))
    with pytest.raises(ProblemException) as bad_fields:
        updates._extract_manifest(incomplete)
    assert bad_fields.value.code == "UPDATE_MANIFEST_INVALID"

    missing_artifact_manifest = {"version": "9.9.9", "artifacts": {"missing.deb": {"size": 1, "sha256": "0" * 64}}}
    missing_artifact = tmp_path / "missing-artifact.partyops-update"
    with zipfile.ZipFile(missing_artifact, "w") as archive:
        archive.writestr("manifest.json", json.dumps(missing_artifact_manifest))
    with pytest.raises(ProblemException) as artifact_missing:
        updates._extract_manifest(missing_artifact)
    assert artifact_missing.value.code == "UPDATE_ARTIFACT_MISSING"

    payload = b"artifact"
    mismatch_manifest = {"version": "9.9.9", "artifacts": {"a.deb": {"size": len(payload), "sha256": "0" * 64}}}
    mismatch = tmp_path / "mismatch.partyops-update"
    with zipfile.ZipFile(mismatch, "w") as archive:
        archive.writestr("manifest.json", json.dumps(mismatch_manifest))
        archive.writestr("a.deb", payload)
    with pytest.raises(ProblemException) as hash_mismatch:
        updates._extract_manifest(mismatch)
    assert hash_mismatch.value.code == "UPDATE_ARTIFACT_HASH_MISMATCH"


def test_update_free_space_and_signature_fail_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(updates.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100))
    with pytest.raises(ProblemException) as full:
        updates._ensure_free_space(tmp_path, 101)
    assert full.value.code == "UPDATE_DISK_FULL"
    settings = SimpleNamespace(update_public_key="invalid-base64")
    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    assert updates._manifest_signature_valid({"signature": "invalid"}) is False
