"""一次性生成 PartyOps 发布签名密钥。

私钥仅写入 artifacts/release-keys，必须由发布负责人离线保管；安装包只携带公钥。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


root = Path(__file__).resolve().parents[1]
private_dir = root / "artifacts" / "release-keys"
private_path = private_dir / "partyops-update-private-key.pem"
public_path = root / "packaging" / "uos" / "update-public-key.txt"
if private_path.exists() or public_path.exists():
    raise SystemExit("发布密钥文件已存在；为避免误轮换，拒绝覆盖。")
private_dir.mkdir(parents=True, exist_ok=True)
key = Ed25519PrivateKey.generate()
private_path.write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)
if os.name != "nt":
    private_path.chmod(0o600)
public_path.write_text(
    base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    + "\n",
    encoding="utf-8",
)
print(f"私钥（离线保管）：{private_path}")
print(f"安装包公钥：{public_path}")
