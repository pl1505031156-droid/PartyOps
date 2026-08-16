"""官网在线更新目录、后台下载与重试路径回归。"""

from __future__ import annotations

import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import UpdateStatus
from app.problems import ProblemException
from app.routers import updates


class Response(io.BytesIO):
    def __init__(self, content: bytes, *, status: int = 200, headers=None):
        super().__init__(content)
        self.status = status
        self.headers = headers or {
            "Content-Length": str(len(content)),
            "Content-Encoding": "",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _catalog(payload: bytes = b"signed-package") -> dict[str, object]:
    return {
        "available": True,
        "current_version": "1.4.3-rc.2",
        "version": "1.4.3-rc.3",
        "title": "安全与多系统更新",
        "release_notes": ["支持应用内升级"],
        "published_at": "2026-08-15T12:00:00+08:00",
        "package_url": "https://www.partyops.cn/releases/partyops.partyops-update",
        "package_size": len(payload),
        "package_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_update_url_requires_exact_https_allowlist(monkeypatch) -> None:
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(update_download_hosts="www.partyops.cn,github.com"),
    )
    assert updates._validate_update_url("https://www.partyops.cn/release#fragment") == (
        "https://www.partyops.cn/release"
    )
    for value in (
        "http://www.partyops.cn/release",
        "https://www.partyops.cn.evil.example/release",
        "https://user@www.partyops.cn/release",
        "https://www.partyops.cn:8443/release",
    ):
        with pytest.raises(ProblemException) as captured:
            updates._validate_update_url(value)
        assert captured.value.code == "UPDATE_URL_NOT_TRUSTED"


def test_trusted_update_request_only_accepts_range_header(monkeypatch) -> None:
    """受信下载器不能被扩展为可注入 Host、身份或压缩头的通用代理。"""

    captured: dict[str, object] = {}

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response(b"ok")

    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(update_download_hosts="www.partyops.cn"),
    )
    monkeypatch.setattr(updates.urllib.request, "build_opener", lambda *_args: Opener())
    with updates._open_trusted_update_url(
        "https://www.partyops.cn/releases/update.partyops-update",
        extra_headers={"Range": "bytes=8-"},
    ) as response:
        assert response.read() == b"ok"
    request = captured["request"]
    assert request.get_header("Range") == "bytes=8-"
    assert request.get_header("Accept-encoding") == "identity"
    assert captured["timeout"] == 30

    for headers in ({"Host": "evil.example"}, {"Authorization": "secret"}, {"Range": "bytes=0-", "Accept-Encoding": "gzip"}):
        with pytest.raises(ValueError, match="不允许的请求头"):
            updates._open_trusted_update_url(
                "https://www.partyops.cn/releases/update.partyops-update",
                extra_headers=headers,
            )


def test_resume_fragment_and_secure_append_reject_invalid_files(tmp_path: Path) -> None:
    """断点片段只能是长度严格匹配的普通文件，不能复用空包或超长包。"""

    fragment = tmp_path / ".download.part"
    assert updates._validated_resume_offset(fragment, 10) == 0

    fragment.write_bytes(b"")
    assert updates._validated_resume_offset(fragment, 10) == 0
    assert not fragment.exists()

    fragment.write_bytes(b"0123456789")
    assert updates._validated_resume_offset(fragment, 10) == 0
    assert not fragment.exists()

    fragment.write_bytes(b"abc")
    assert updates._validated_resume_offset(fragment, 10) == 3
    with pytest.raises(OSError, match="续传前被替换"):
        updates._open_partial_download(fragment, offset=2)
    with updates._open_partial_download(fragment, offset=3) as handle:
        handle.write(b"d")
    assert fragment.read_bytes() == b"abcd"

    fresh = tmp_path / ".fresh.part"
    with updates._open_partial_download(fresh, offset=0) as handle:
        handle.write(b"new")
    assert fresh.read_bytes() == b"new"


def test_duplicate_json_hook_rejects_second_value() -> None:
    assert updates._reject_duplicate_json([("version", "1")]) == {"version": "1"}
    with pytest.raises(ValueError, match="重复字段"):
        updates._reject_duplicate_json([("version", "1"), ("version", "2")])


def test_fetch_online_catalog_validates_signature_shape_and_version(monkeypatch) -> None:
    release = _catalog()
    document = {
        "format": "partyops-update-channel",
        "format_version": 1,
        "release": {
            "version": release["version"],
            "title": release["title"],
            "release_notes": release["release_notes"],
            "published_at": release["published_at"],
            "package_url": release["package_url"],
            "package_size": release["package_size"],
            "package_sha256": release["package_sha256"],
        },
        "signature": "signed",
    }
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(
            update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
            update_download_hosts="www.partyops.cn",
        ),
    )
    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: True)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(json.dumps(document).encode()),
    )
    result = updates.fetch_online_update_catalog()
    assert result["available"] is False  # 测试源码本身已是 rc.3。
    assert result["version"] == "1.4.3-rc.3"

    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: False)
    with pytest.raises(ProblemException) as captured:
        updates.fetch_online_update_catalog()
    assert captured.value.code == "UPDATE_CATALOG_SIGNATURE_INVALID"


def test_fetch_v2_catalog_selects_only_current_platform_package(monkeypatch) -> None:
    release = _catalog(b"windows-package")
    document = {
        "format": "partyops-update-channel",
        "format_version": 2,
        "release": {
            "version": release["version"],
            "title": release["title"],
            "release_notes": release["release_notes"],
            "published_at": release["published_at"],
            "platform_packages": {
                "windows": {
                    "amd64": {
                        "package_url": release["package_url"],
                        "package_size": release["package_size"],
                        "package_sha256": release["package_sha256"],
                    }
                },
                "linux-deb": {
                    "arm64": {
                        "package_url": "https://www.partyops.cn/releases/arm64.partyops-update",
                        "package_size": 999,
                        "package_sha256": "a" * 64,
                    }
                },
            },
        },
        "signature": "signed",
    }
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(
            update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
            update_download_hosts="www.partyops.cn",
        ),
    )
    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: True)
    monkeypatch.setattr(
        updates,
        "detect_platform_info",
        lambda: {"platform_family": "windows", "distribution": "windows", "package_format": "exe"},
    )
    monkeypatch.setattr(updates, "normalize_architecture", lambda: "amd64")
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(json.dumps(document).encode()),
    )
    selected = updates.fetch_online_update_catalog()
    assert selected["package_url"] == release["package_url"]
    assert selected["package_size"] == release["package_size"]


def test_fetch_online_catalog_rejects_duplicate_json_and_encoded_response(monkeypatch) -> None:
    settings = SimpleNamespace(
        update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
        update_download_hosts="www.partyops.cn",
    )
    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    duplicate = b'{"format":"a","format":"b"}'
    monkeypatch.setattr(updates, "_open_trusted_update_url", lambda _url: Response(duplicate))
    with pytest.raises(ProblemException) as captured:
        updates.fetch_online_update_catalog()
    assert captured.value.code == "UPDATE_CATALOG_UNAVAILABLE"

    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(b"{}", headers={"Content-Encoding": "gzip"}),
    )
    with pytest.raises(ProblemException) as captured:
        updates.fetch_online_update_catalog()
    assert captured.value.code == "UPDATE_CATALOG_UNAVAILABLE"


def test_fetch_online_catalog_rejects_ambiguous_or_incomplete_signed_contract(monkeypatch) -> None:
    release = _catalog()
    base_release = {
        "version": release["version"],
        "title": release["title"],
        "release_notes": release["release_notes"],
        "published_at": release["published_at"],
        "package_url": release["package_url"],
        "package_size": release["package_size"],
        "package_sha256": release["package_sha256"],
    }
    base = {
        "format": "partyops-update-channel",
        "format_version": 1,
        "release": base_release,
        "signature": "signed",
    }
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(
            update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
            update_download_hosts="www.partyops.cn",
        ),
    )
    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: True)

    for document, expected_code in (
        ({**base, "format_version": True}, "UPDATE_CATALOG_SIGNATURE_INVALID"),
        ({**base, "release": []}, "UPDATE_CATALOG_INVALID"),
        ({**base, "release": {**base_release, "package_size": 0}}, "UPDATE_CATALOG_INVALID"),
        ({**base, "release": {**base_release, "package_sha256": "bad"}}, "UPDATE_CATALOG_INVALID"),
        ({**base, "release": {**base_release, "release_notes": []}}, "UPDATE_CATALOG_INVALID"),
        ({**base, "release": {**base_release, "version": "not a version"}}, "UPDATE_CATALOG_INVALID"),
        ({**base, "release": {**base_release, "published_at": "2026-08-15 12:00:00"}}, "UPDATE_CATALOG_INVALID"),
    ):
        monkeypatch.setattr(
            updates,
            "_open_trusted_update_url",
            lambda _url, payload=json.dumps(document).encode(): Response(payload),
        )
        with pytest.raises(ProblemException) as captured:
            updates.fetch_online_update_catalog()
        assert captured.value.code == expected_code


def test_fetch_v2_catalog_reports_platform_gate_without_mislabeling_catalog(monkeypatch) -> None:
    release = _catalog()
    document = {
        "format": "partyops-update-channel",
        "format_version": 2,
        "release": {
            "version": release["version"],
            "title": release["title"],
            "release_notes": release["release_notes"],
            "published_at": release["published_at"],
            "platform_packages": {"linux-deb": {"arm64": {}}},
        },
        "signature": "signed",
    }
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(
            update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
            update_download_hosts="www.partyops.cn",
        ),
    )
    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: True)
    monkeypatch.setattr(updates, "update_platform_key", lambda _info: "windows")
    monkeypatch.setattr(updates, "detect_platform_info", lambda: {})
    monkeypatch.setattr(updates, "normalize_architecture", lambda: "amd64")
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(json.dumps(document).encode()),
    )
    result = updates.fetch_online_update_catalog()
    assert result["available"] is False
    assert result["target_available"] is False
    assert "windows/amd64" in str(result["availability_message"])
    assert "package_url" not in result


def test_fetch_v2_catalog_rejects_malformed_selected_platform_record(monkeypatch) -> None:
    release = _catalog()
    document = {
        "format": "partyops-update-channel",
        "format_version": 2,
        "release": {
            "version": release["version"],
            "title": release["title"],
            "release_notes": release["release_notes"],
            "published_at": release["published_at"],
            "platform_packages": {"windows": {"amd64": {}}},
        },
        "signature": "signed",
    }
    monkeypatch.setattr(
        updates,
        "get_settings",
        lambda: SimpleNamespace(
            update_catalog_url="https://www.partyops.cn/releases/update-v3.json",
            update_download_hosts="www.partyops.cn",
        ),
    )
    monkeypatch.setattr(updates, "_manifest_signature_valid", lambda _value: True)
    monkeypatch.setattr(updates, "update_platform_key", lambda _info: "windows")
    monkeypatch.setattr(updates, "detect_platform_info", lambda: {})
    monkeypatch.setattr(updates, "normalize_architecture", lambda: "amd64")
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(json.dumps(document).encode()),
    )
    with pytest.raises(ProblemException) as captured:
        updates.fetch_online_update_catalog()
    assert captured.value.code == "UPDATE_CATALOG_INVALID"

def test_online_download_validates_outer_hash_inner_manifest_and_persists(monkeypatch, tmp_path: Path) -> None:
    payload = b"signed-update-package"
    catalog = _catalog(payload)
    settings = SimpleNamespace(updates_dir=tmp_path)
    package = SimpleNamespace(
        id="package-1",
        filename="online-package-1.partyops-update",
        version=catalog["version"],
        min_version="",
        schema_revision="",
        manifest={},
        sha256=catalog["package_sha256"],
        signature_valid=False,
        status=UpdateStatus.UPLOADED,
    )

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(payload),
    )
    monkeypatch.setattr(
        updates,
        "_extract_manifest",
        lambda _path: {
            "version": catalog["version"],
            "min_version": "1.4.3-rc.2",
            "schema_revision": "0019",
            "release_notes": ["应用内升级"],
            "artifacts": {"installer.exe": {"size": 1, "sha256": "0" * 64}},
        },
    )
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.VALIDATED
    assert package.signature_valid is True
    assert package.manifest["online_download"]["download_state"] == "ready"
    assert (tmp_path / package.filename).read_bytes() == payload


def test_online_download_resumes_verified_partial_with_strict_content_range(
    monkeypatch, tmp_path: Path
) -> None:
    payload = b"signed-update-package-with-resume"
    catalog = _catalog(payload)
    settings = SimpleNamespace(updates_dir=tmp_path)
    package = SimpleNamespace(
        id="package-resume",
        filename="online-package-resume.partyops-update",
        version=catalog["version"],
        min_version="",
        schema_revision="",
        manifest={},
        sha256=catalog["package_sha256"],
        signature_valid=False,
        status=UpdateStatus.UPLOADED,
    )

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    resume_at = 11
    (tmp_path / f".{package.id}.incoming").write_bytes(payload[:resume_at])
    seen_headers: list[dict[str, str] | None] = []

    def open_range(_url: str, *, extra_headers=None):
        seen_headers.append(extra_headers)
        return Response(
            payload[resume_at:],
            status=206,
            headers={
                "Content-Length": str(len(payload) - resume_at),
                "Content-Encoding": "",
                "Content-Range": f"bytes {resume_at}-{len(payload) - 1}/{len(payload)}",
            },
        )

    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(updates, "_open_trusted_update_url", open_range)
    monkeypatch.setattr(
        updates,
        "_extract_manifest",
        lambda _path: {
            "version": catalog["version"],
            "min_version": "1.4.3-rc.3",
            "schema_revision": "0019",
            "release_notes": ["应用内升级"],
            "artifacts": {"installer.exe": {"size": 1, "sha256": "0" * 64}},
        },
    )

    updates._download_online_update(package.id, catalog)

    assert seen_headers == [{"Range": f"bytes={resume_at}-"}]
    assert package.status == UpdateStatus.VALIDATED
    assert (tmp_path / package.filename).read_bytes() == payload


def test_online_download_keeps_incomplete_partial_for_next_retry(
    monkeypatch, tmp_path: Path
) -> None:
    complete = b"longer-signed-update-package"
    truncated = complete[:12]
    catalog = _catalog(complete)
    settings = SimpleNamespace(updates_dir=tmp_path)
    package = SimpleNamespace(id="package-interrupted", manifest={}, status=UpdateStatus.UPLOADED)

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url, **_kwargs: Response(
            truncated,
            headers={"Content-Length": str(len(complete)), "Content-Encoding": ""},
        ),
    )

    updates._download_online_update(package.id, catalog)

    partial = tmp_path / f".{package.id}.incoming"
    assert partial.read_bytes() == truncated
    assert package.status == UpdateStatus.FAILED
    assert "中断位置继续" in package.manifest["download_message"]


def test_online_download_failure_is_chinese_and_keeps_current_version(monkeypatch, tmp_path: Path) -> None:
    payload = b"tampered"
    catalog = _catalog(b"expected")
    settings = SimpleNamespace(updates_dir=tmp_path)
    package = SimpleNamespace(
        id="package-failed",
        manifest={},
        status=UpdateStatus.UPLOADED,
    )

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: settings)
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda _url: Response(payload),
    )
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED
    assert package.manifest["download_state"] == "failed"
    assert package.manifest["download_message"].startswith("安全下载未完成")
    assert not list(tmp_path.glob("*.incoming"))


def test_prepare_online_update_reuses_state_and_starts_background(monkeypatch, tmp_path: Path) -> None:
    catalog = _catalog()
    started: list[str] = []
    added: list[object] = []

    class Session:
        def scalar(self, _query):
            return None

        def add(self, value):
            added.append(value)

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(
        updates,
        "_start_online_download",
        lambda package_id, _catalog: started.append(package_id) or True,
    )
    monkeypatch.setattr(updates, "write_audit", lambda *_args, **_kwargs: None)
    package = updates.prepare_online_update(
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
        SimpleNamespace(id="admin-1"),
        Session(),
    )
    assert package.status == UpdateStatus.UPLOADED
    assert package.signature_valid is False
    assert started == [package.id]
    assert added == [package]

    catalog["available"] = False
    with pytest.raises(ProblemException) as captured:
        updates.prepare_online_update(
            SimpleNamespace(client=None),
            SimpleNamespace(id="admin-1"),
            Session(),
        )
    assert captured.value.code == "UPDATE_ALREADY_CURRENT"

    catalog["target_available"] = False
    catalog["availability_message"] = "arm64 制品尚未通过门禁"
    with pytest.raises(ProblemException) as unavailable:
        updates.prepare_online_update(
            SimpleNamespace(client=None),
            SimpleNamespace(id="admin-1"),
            Session(),
        )
    assert unavailable.value.code == "UPDATE_TARGET_UNAVAILABLE"
    assert "arm64" in unavailable.value.detail


def test_prepare_online_update_reuses_only_a_complete_verified_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = b"signed-package"
    catalog = _catalog(payload)
    started: list[str] = []
    monkeypatch.setattr(updates, "fetch_online_update_catalog", lambda: catalog)
    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(updates, "_start_online_download", lambda package_id, _catalog: started.append(package_id) or True)
    monkeypatch.setattr(updates, "write_audit", lambda *_args, **_kwargs: None)

    package = SimpleNamespace(
        id="cached-package",
        filename="cached.partyops-update",
        version=catalog["version"],
        manifest={},
        sha256=catalog["package_sha256"],
        signature_valid=True,
        status=UpdateStatus.VALIDATED,
    )

    class Session:
        def scalar(self, _query):
            return package

        def commit(self):
            return None

        def refresh(self, _value):
            return None

    cached = tmp_path / package.filename
    cached.write_bytes(payload)
    assert updates.prepare_online_update(
        SimpleNamespace(client=None),
        SimpleNamespace(id="admin"),
        Session(),
    ) is package
    assert started == []

    # 已验证记录的文件损坏时回到“待下载”，不能继续信任数据库里的旧布尔值。
    cached.write_bytes(b"x" * len(payload))
    resumed = updates.prepare_online_update(
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
        SimpleNamespace(id="admin"),
        Session(),
    )
    assert resumed.status == UpdateStatus.UPLOADED
    assert resumed.signature_valid is False
    assert started == [package.id]

    package.status = UpdateStatus.APPLYING
    with pytest.raises(ProblemException) as missing_active:
        updates.prepare_online_update(
            SimpleNamespace(client=None),
            SimpleNamespace(id="admin"),
            Session(),
        )
    assert missing_active.value.code == "UPDATE_LOCAL_PACKAGE_MISSING"


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (500, {"Content-Length": "14", "Content-Encoding": ""}),
        (200, {"Content-Length": "14", "Content-Encoding": "gzip"}),
        (200, {"Content-Length": "bad", "Content-Encoding": ""}),
        (200, {"Content-Length": "13", "Content-Encoding": ""}),
    ],
)
def test_online_download_rejects_untrusted_http_response_shapes(
    monkeypatch,
    tmp_path: Path,
    status: int,
    headers: dict[str, str],
) -> None:
    payload = b"signed-package"
    catalog = _catalog(payload)
    package = SimpleNamespace(id=f"invalid-{status}-{headers['Content-Length']}", manifest={}, status=UpdateStatus.UPLOADED)

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda *_args, **_kwargs: Response(payload, status=status, headers=headers),
    )
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED
    assert not list(tmp_path.glob("*.incoming"))


def test_online_download_rejects_overrun_wrong_manifest_and_conflicting_cache(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = b"signed-package"
    catalog = _catalog(payload)
    package = SimpleNamespace(id="adversarial-download", manifest={}, status=UpdateStatus.UPLOADED)

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda *_args, **_kwargs: Response(
            payload + b"overflow",
            headers={"Content-Length": str(len(payload)), "Content-Encoding": ""},
        ),
    )
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED

    monkeypatch.setattr(updates, "_open_trusted_update_url", lambda *_args, **_kwargs: Response(payload))
    monkeypatch.setattr(updates, "_extract_manifest", lambda _path: {"version": "9.9.9"})
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED

    expected_final = tmp_path / f"partyops_{catalog['version']}_{str(catalog['package_sha256'])[:12]}.partyops-update"
    expected_final.write_bytes(b"conflict")
    monkeypatch.setattr(updates, "_extract_manifest", lambda _path: {"version": catalog["version"]})
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED


def test_online_download_reuses_identical_final_and_removes_orphan_without_db_record(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = b"signed-package"
    catalog = _catalog(payload)
    package_id = "orphan-download"
    final_path = tmp_path / f"partyops_{catalog['version']}_{str(catalog['package_sha256'])[:12]}.partyops-update"
    final_path.write_bytes(payload)

    class Session:
        def get(self, _model, _identity):
            return None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(updates, "_open_trusted_update_url", lambda *_args, **_kwargs: Response(payload))
    monkeypatch.setattr(updates, "_extract_manifest", lambda _path: {"version": catalog["version"]})
    updates._download_online_update(package_id, catalog)
    assert not final_path.exists()


def test_online_download_rejects_wrong_resume_range_and_reports_large_progress(
    monkeypatch,
    tmp_path: Path,
) -> None:
    payload = b"p" * (9 * 1024 * 1024)
    catalog = _catalog(payload)
    package = SimpleNamespace(
        id="large-download",
        filename="pending.partyops-update",
        manifest={},
        status=UpdateStatus.UPLOADED,
    )

    class Session:
        def get(self, _model, identity):
            return package if identity == package.id else None

        def commit(self):
            return None

    @contextmanager
    def factory():
        yield Session()

    monkeypatch.setattr(updates, "get_settings", lambda: SimpleNamespace(updates_dir=tmp_path))
    monkeypatch.setattr(updates.db_runtime, "session_factory", factory)
    monkeypatch.setattr(updates, "_ensure_free_space", lambda *_args: None)
    monkeypatch.setattr(updates, "_extract_manifest", lambda _path: {"version": catalog["version"]})
    progress: list[str] = []
    original_progress = updates._set_online_download_progress

    def record_progress(*args, **kwargs):
        progress.append(str(kwargs.get("message", "")))
        return original_progress(*args, **kwargs)

    monkeypatch.setattr(updates, "_set_online_download_progress", record_progress)
    monkeypatch.setattr(updates, "_open_trusted_update_url", lambda *_args, **_kwargs: Response(payload))
    updates._download_online_update(package.id, catalog)
    assert any("实时校验" in message for message in progress)

    package.id = "bad-range"
    partial = tmp_path / f".{package.id}.incoming"
    partial.write_bytes(payload[:1024])
    monkeypatch.setattr(
        updates,
        "_open_trusted_update_url",
        lambda *_args, **_kwargs: Response(
            payload[1024:],
            status=206,
            headers={
                "Content-Length": str(len(payload) - 1024),
                "Content-Encoding": "",
                "Content-Range": f"bytes 999-{len(payload) - 1}/{len(payload)}",
            },
        ),
    )
    updates._download_online_update(package.id, catalog)
    assert package.status == UpdateStatus.FAILED
