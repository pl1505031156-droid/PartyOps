"""从冻结的轻量更新包生成官网 Ed25519 签名在线更新目录。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


VERSION = "1.4.3-rc.4"
TARGETS = (
    ("windows", "amd64"),
    ("windows7", "amd64"),
    ("windows7", "x86"),
    ("linux-deb", "amd64"),
    ("linux-deb", "arm64"),
    ("linux-rpm", "amd64"),
    ("linux-rpm", "arm64"),
)
RELEASE_NOTES = [
    "支持系统内检查、后台下载和一键原位升级，失败自动回滚且保留数据",
    "更新流量按当前系统与 CPU 架构精确匹配，无需重新寻找安装包",
    "新增个人模式、自定义数据目录和可选彻底卸载",
    "修复服务启动、首次管理员、注册表权限、空白页及中文诊断问题",
    "新增麒麟、UOS、deepin 与 openEuler 安装制品；Win7 安全门禁未通过，本版不提供",
]


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data.strip(), validate=True))
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("更新目录签名必须使用 Ed25519 私钥")
    return key


def _public_key(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _validated_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("更新包基础地址必须是无凭据、无参数的标准 HTTPS 地址")
    return value.rstrip("/")


def resolve_targets(values: list[str] | None) -> tuple[tuple[str, str], ...]:
    """解析显式目录目标；默认完整矩阵，显式模式用于独立阻断单个制品。"""

    if not values:
        return TARGETS
    selected: list[tuple[str, str]] = []
    for value in values:
        parts = value.split("/", 1)
        target = (parts[0], parts[1]) if len(parts) == 2 else ("", "")
        if target not in TARGETS:
            raise ValueError(f"未知发布目标：{value}")
        if target in selected:
            raise ValueError(f"发布目标重复：{value}")
        selected.append(target)
    return tuple(selected)


def generate_catalog(
    *,
    packages_dir: Path,
    private_key_path: Path,
    public_key_path: Path,
    package_base_url: str,
    published_at: str,
    targets: tuple[tuple[str, str], ...] = TARGETS,
) -> dict[str, object]:
    timestamp = datetime.fromisoformat(published_at)
    if timestamp.tzinfo is None:
        raise ValueError("上传时间必须包含时区")
    key = _load_key(private_key_path)
    public_key = _public_key(key)
    if public_key != public_key_path.read_text(encoding="ascii").strip():
        raise ValueError("发布私钥与安装包信任公钥不匹配")
    base_url = _validated_base_url(package_base_url)
    if not targets or any(target not in TARGETS for target in targets) or len(set(targets)) != len(targets):
        raise ValueError("更新目录目标必须是非空、无重复的受支持平台集合")
    platform_packages: dict[str, dict[str, dict[str, object]]] = {}
    for platform_name, architecture in targets:
        filename = f"partyops_{VERSION}_{platform_name}_{architecture}.partyops-update"
        package = packages_dir / filename
        if not package.is_file():
            raise FileNotFoundError(f"缺少轻量更新包：{filename}")
        platform_packages.setdefault(platform_name, {})[architecture] = {
            "package_url": f"{base_url}/{urllib.parse.quote(filename)}",
            "package_size": package.stat().st_size,
            "package_sha256": _hash(package),
        }
    catalog: dict[str, object] = {
        "format": "partyops-update-channel",
        # v3 保留 v2 的平台/架构二维索引，并把该结构正式确立为多系统
        # 更新契约；rc.2 客户端仍可读取 v2，rc.3 同时接受 v2/v3。
        "format_version": 3,
        "release": {
            "version": VERSION,
            "title": "PartyOps 多系统适配与专业级应用内升级",
            "release_notes": RELEASE_NOTES,
            "published_at": published_at,
            "platform_packages": platform_packages,
        },
        "public_key": public_key,
    }
    canonical = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    catalog["signature"] = base64.b64encode(key.sign(canonical)).decode("ascii")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages-dir", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--package-base-url", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--target",
        action="append",
        metavar="平台/架构",
        help="仅收录已通过独立门禁的目标；可重复。",
    )
    args = parser.parse_args()
    try:
        targets = resolve_targets(args.target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    catalog = generate_catalog(
        packages_dir=args.packages_dir.resolve(),
        private_key_path=args.private_key.resolve(),
        public_key_path=args.public_key.resolve(),
        package_base_url=args.package_base_url,
        published_at=args.published_at,
        targets=targets,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=args.output.parent,
            prefix=f".{args.output.name}.",
            suffix=".incoming",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(catalog, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"官网签名更新目录已生成：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
