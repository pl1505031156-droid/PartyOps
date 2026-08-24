"""在任何制品或模型构建前验证正式 Ed25519 发布密钥与客户端信任根。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def load_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file() or path.is_symlink():
        raise ValueError("正式发布私钥必须是本机普通文件，不能使用链接")
    metadata = path.stat()
    if metadata.st_size <= 0 or metadata.st_size > 64 * 1024:
        raise ValueError("正式发布私钥文件大小异常")
    if os.name != "nt" and metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("正式发布私钥权限过宽，请限制为仅当前用户可读")
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        try:
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(data.strip(), validate=True)
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("正式发布私钥不是可识别的 Ed25519 PEM 或原始 Base64") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("正式发布私钥必须使用 Ed25519")
    return key


def verify(private_key_path: Path, public_key_path: Path) -> str:
    if not public_key_path.is_file() or public_key_path.is_symlink():
        raise ValueError("客户端内置信任公钥文件不存在或不是普通文件")
    try:
        trusted = base64.b64decode(
            public_key_path.read_text(encoding="ascii").strip(), validate=True
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("客户端内置信任公钥不是有效 Base64") from exc
    if len(trusted) != 32:
        raise ValueError("客户端内置信任公钥长度不是 Ed25519 原始公钥长度")
    key = load_private_key(private_key_path)
    actual = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if actual != trusted:
        raise ValueError("正式发布私钥与客户端内置信任公钥不匹配")
    # 只输出公钥指纹，绝不输出、复制或记录私钥内容。
    return hashlib.sha256(actual).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="验证更新目录、安装包和模型包共用的正式 Ed25519 发布密钥"
    )
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument(
        "--public-key",
        type=Path,
        default=Path("packaging/uos/update-public-key.txt"),
    )
    args = parser.parse_args()
    try:
        fingerprint = verify(args.private_key.resolve(), args.public_key.resolve())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"正式发布签名门禁失败：{exc}") from exc
    print(f"正式发布签名门禁通过；Ed25519 公钥 SHA-256：{fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
