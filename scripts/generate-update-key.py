"""一次性生成 PartyOps 发布签名密钥。

私钥仅写入 artifacts/release-keys，必须由发布负责人离线保管；安装包只携带公钥。
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument(
    "--rotate-lost-private-key",
    action="store_true",
    help="旧私钥确认丢失时，归档旧公钥并为下一桥接版本生成新密钥",
)
args = parser.parse_args()
private_dir = root / "artifacts" / "release-keys"
private_path = private_dir / "partyops-update-private-key.pem"
public_path = root / "packaging" / "uos" / "update-public-key.txt"
legacy_public_path = private_dir / "partyops-update-public-key-legacy-rc2.txt"
if private_path.exists():
    raise SystemExit("发布密钥文件已存在；为避免误轮换，拒绝覆盖。")
private_dir.mkdir(parents=True, exist_ok=True)
if public_path.exists():
    if not args.rotate_lost_private_key:
        raise SystemExit("发布公钥已存在；为避免误轮换，拒绝覆盖。")
    if legacy_public_path.exists():
        raise SystemExit("旧公钥审计文件已存在；拒绝再次轮换。")
    legacy_public_path.write_text(
        public_path.read_text(encoding="ascii").strip() + "\n",
        encoding="ascii",
    )
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
