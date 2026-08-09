"""主机本地短期机密的加密封装。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


def _key_path() -> Path:
    return get_settings().secrets_dir / "local-recovery-fernet.key"


def _fernet() -> Fernet:
    """并发安全地创建只属于本机的加密密钥。"""

    path = _key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def encrypt_local_json(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_local_json(value: str) -> dict[str, Any]:
    try:
        payload = _fernet().decrypt(value.encode("ascii"))
        decoded = json.loads(payload.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("本机恢复凭据无法解密") from exc
    if not isinstance(decoded, dict):
        raise ValueError("本机恢复凭据格式无效")
    return decoded
