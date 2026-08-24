"""一次性生成 PartyOps 发布签名密钥。

私钥仅写入 artifacts/release-keys，必须由发布负责人离线保管；安装包只携带公钥。
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
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
public_next_path = public_path.with_name(f"{public_path.name}.next")
legacy_public_path = private_dir / "partyops-update-public-key-legacy-rc2.txt"
if private_path.exists() or public_next_path.exists():
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
if os.name == "nt":
    account = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    identity = f"{account}\\{username}" if account and username else username
    if not identity:
        private_path.unlink(missing_ok=True)
        legacy_public_path.unlink(missing_ok=True)
        raise SystemExit("无法识别当前 Windows 账号；生产私钥已撤销，未轮换信任根。")
    try:
        subprocess.run(
            [
                "icacls.exe",
                str(private_path),
                "/inheritance:r",
                "/grant:r",
                f"{identity}:(R,W)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        private_path.unlink(missing_ok=True)
        legacy_public_path.unlink(missing_ok=True)
        raise SystemExit("无法收紧生产私钥 ACL；私钥已撤销，未轮换信任根。") from exc
else:
    private_dir.chmod(0o700)
    private_path.chmod(0o600)
try:
    public_next_path.write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii")
        + "\n",
        encoding="utf-8",
    )
    os.replace(public_next_path, public_path)
except OSError as exc:
    public_next_path.unlink(missing_ok=True)
    private_path.unlink(missing_ok=True)
    legacy_public_path.unlink(missing_ok=True)
    raise SystemExit("无法原子更新客户端信任公钥；私钥已撤销，未轮换信任根。") from exc
print(f"私钥（离线保管）：{private_path}")
print(f"安装包公钥：{public_path}")
