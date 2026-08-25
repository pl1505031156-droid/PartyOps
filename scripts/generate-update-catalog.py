"""从冻结的单平台签名更新包生成官网 Ed25519 在线更新目录。"""

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

VERSION = "1.4.5-rc.3"
TARGETS = (
    ("windows", "amd64"),
    ("windows7", "amd64"),
    ("windows7", "x86"),
    ("linux-deb", "amd64"),
    ("linux-deb", "arm64"),
    ("linux-rpm", "amd64"),
    ("linux-rpm", "arm64"),
    ("macos", "amd64"),
    ("macos", "arm64"),
)
RELEASE_NOTES = [
    "公文规范排版改为系统内嵌流程，文件只在当前电脑诊断、排版、复检和导出",
    "新增通用台账导入：全量剖析、字段确认、重复处置、原子提交与安全撤销",
    "发展党员使用真实进度时间轴，已发生事实不覆盖，未来节点按法规和参考规则重算",
    "补齐发展党员、会议、文档、学习、工作日志、目录、备份和模型包生命周期闭环",
    "修复跨账号自定义程序目录 ACL 交接及非 C 盘数据目录安全接管",
    "Needle 2 仅提供签名原生平台包，意图结果只生成需要用户确认的安全预览",
    "任务、党务、周期与桌面提醒完成改期、去重、静默时段和协同隐私回归",
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
        raise TypeError("更新目录签名必须使用 Ed25519 私钥")
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


def _validated_package_url(value: str, filename: str) -> str:
    """校验 Cloud Studio 为单个制品返回的独立公网下载地址。"""

    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or urllib.parse.unquote(Path(parsed.path).name) != filename
        or "/downloads/" not in parsed.path
    ):
        raise ValueError(f"更新包地址必须是指向 /downloads/{filename} 的标准 HTTPS 地址")
    return value


def _load_package_url_map(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("更新包地址映射必须是字符串键值 JSON 对象")
    return payload


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
    package_base_url: str | None,
    package_url_map: dict[str, str] | None = None,
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
    if (package_base_url is None) == (package_url_map is None):
        raise ValueError("更新包基础地址与独立地址映射必须且只能提供一个")
    base_url = _validated_base_url(package_base_url) if package_base_url is not None else None
    if not targets or any(target not in TARGETS for target in targets) or len(set(targets)) != len(targets):
        raise ValueError("更新目录目标必须是非空、无重复的受支持平台集合")
    platform_packages: dict[str, dict[str, dict[str, object]]] = {}
    for platform_name, architecture in targets:
        filename = f"partyops_{VERSION}_{platform_name}_{architecture}.partyops-update"
        package = packages_dir / filename
        if not package.is_file():
            raise FileNotFoundError(f"缺少单平台签名更新包：{filename}")
        if package_url_map is not None:
            target_key = f"{platform_name}/{architecture}"
            try:
                package_url = _validated_package_url(package_url_map[target_key], filename)
            except KeyError as exc:
                raise ValueError(f"更新包地址映射缺少目标：{target_key}") from exc
        else:
            assert base_url is not None
            package_url = f"{base_url}/{urllib.parse.quote(filename)}"
        platform_packages.setdefault(platform_name, {})[architecture] = {
            "package_url": package_url,
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
            # rc.2 轮换正式信任根；旧客户端须先使用完整安装器升级。
            "min_version": VERSION,
            "title": "PartyOps 内嵌公文排版、通用台账导入与全流程时间轴",
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
    url_group = parser.add_mutually_exclusive_group(required=True)
    url_group.add_argument("--package-base-url")
    url_group.add_argument(
        "--package-url-map",
        type=Path,
        help="平台/架构到 Cloud Studio 独立 /downloads/ HTTPS 地址的 JSON 映射。",
    )
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
        package_url_map=(
            _load_package_url_map(args.package_url_map.resolve())
            if args.package_url_map is not None
            else None
        ),
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
