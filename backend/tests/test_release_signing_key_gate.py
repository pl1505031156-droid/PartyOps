from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify-release-signing-key.py"
SPEC = importlib.util.spec_from_file_location("partyops_release_signing_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_pair(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    private = root / "private.pem"
    public = root / "public.txt"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")
        + "\n",
        encoding="ascii",
    )
    return private, public


def test_matching_release_key_returns_only_public_fingerprint(tmp_path: Path) -> None:
    private, public = _write_pair(tmp_path)
    fingerprint = MODULE.verify(private, public)
    assert len(fingerprint) == 64
    assert private.read_text(encoding="ascii").strip() not in fingerprint


def test_mismatched_or_linked_release_key_is_rejected(tmp_path: Path) -> None:
    private, _public = _write_pair(tmp_path / "first")
    _other_private, other_public = _write_pair(tmp_path / "second")
    with pytest.raises(ValueError, match="不匹配"):
        MODULE.verify(private, other_public)
    link = tmp_path / "private-link.pem"
    try:
        link.symlink_to(private)
    except OSError:
        return
    with pytest.raises(ValueError, match="普通文件"):
        MODULE.verify(link, other_public)
