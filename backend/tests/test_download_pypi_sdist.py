"""PyPI 固定源码下载器的来源、摘要与边界回归。"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "partyops_download_pypi_sdist", ROOT / "scripts" / "download-pypi-sdist.py"
)
assert SPEC and SPEC.loader
downloader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloader)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def metadata(payload: bytes, **overrides) -> bytes:
    item = {
        "packagetype": "sdist",
        "url": "https://files.pythonhosted.org/packages/demo-1.0.tar.gz",
        "filename": "demo-1.0.tar.gz",
        "size": len(payload),
        "digests": {"sha256": hashlib.sha256(payload).hexdigest()},
        "upload_time_iso_8601": "2026-08-14T00:00:00Z",
    }
    item.update(overrides)
    return json.dumps({"urls": [item]}).encode()


def test_download_verifies_official_metadata_and_writes_evidence(
    monkeypatch, tmp_path
) -> None:
    payload = b"audited source archive"
    responses = iter((Response(metadata(payload)), Response(payload)))
    monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda *_a, **_k: next(responses))

    result = downloader.download("demo==1.0", tmp_path)

    assert result.read_bytes() == payload
    evidence = json.loads((tmp_path / "demo-1.0.tar.gz.pypi.json").read_text())
    assert evidence["sha256"] == hashlib.sha256(payload).hexdigest()
    assert evidence["size"] == len(payload)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("requirement", ["demo", "demo==1==2", "../demo==1", "==1"])
def test_download_rejects_unpinned_or_unsafe_requirement(requirement, tmp_path) -> None:
    with pytest.raises(ValueError, match="依赖"):
        downloader.download(requirement, tmp_path)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"url": "http://files.pythonhosted.org/demo.tar.gz"}, "来源不受信"),
        ({"url": "https://example.com/demo.tar.gz"}, "来源不受信"),
        ({"filename": "other.tar.gz"}, "文件名"),
        ({"digests": {"sha256": "bad"}}, "摘要或体积"),
        ({"size": 0}, "摘要或体积"),
    ],
)
def test_download_rejects_untrusted_metadata(monkeypatch, tmp_path, overrides, message) -> None:
    monkeypatch.setattr(
        downloader.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(metadata(b"payload", **overrides)),
    )
    with pytest.raises(ValueError, match=message):
        downloader.download("demo==1.0", tmp_path)


def test_download_rejects_ambiguous_sdist(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        downloader.urllib.request,
        "urlopen",
        lambda *_a, **_k: Response(b'{"urls": []}'),
    )
    with pytest.raises(ValueError, match="sdist 数量异常"):
        downloader.download("demo==1.0", tmp_path)


def test_download_rejects_payload_mismatch_and_removes_partial(monkeypatch, tmp_path) -> None:
    expected = b"expected"
    responses = iter((Response(metadata(expected)), Response(b"tampered")))
    monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda *_a, **_k: next(responses))

    with pytest.raises(ValueError, match="大小或 SHA-256"):
        downloader.download("demo==1.0", tmp_path)

    assert not list(tmp_path.iterdir())
