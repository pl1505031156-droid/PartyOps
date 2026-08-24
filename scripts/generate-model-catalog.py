#!/usr/bin/env python3
"""从已冻结并验签的模型包生成官网签名模型目录。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _private_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data.strip(), validate=True))
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("模型目录签名必须使用 Ed25519 私钥")
    return key


def _public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _read_pack(path: Path, trusted_public_raw: bytes) -> dict[str, object]:
    if path.suffix != ".partyops-modelpack" or not path.is_file():
        raise ValueError(f"模型包路径无效：{path}")
    if not re.fullmatch(r"[0-9A-Za-z._-]+\.partyops-modelpack", path.name):
        raise ValueError(f"模型包文件名不安全：{path.name}")
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"模型包清单不可读：{path.name}") from exc
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError(f"模型包没有逐文件清单：{path.name}")
        expected_names = {"manifest.json", *files.keys()}
        actual_names = {item.filename for item in archive.infolist() if not item.is_dir()}
        if actual_names != expected_names:
            raise ValueError(f"模型包成员与签名清单不一致：{path.name}")
        for filename, metadata in files.items():
            if not isinstance(filename, str):
                raise TypeError(f"模型包成员路径无效：{path.name}")
            pure = PurePosixPath(filename)
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or "\\" in filename
                or not isinstance(metadata, dict)
            ):
                raise ValueError(f"模型包成员路径或元数据无效：{path.name}")
            expected_size = metadata.get("size")
            expected_hash = metadata.get("sha256")
            if not isinstance(expected_size, int) or expected_size < 0 or not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
                raise ValueError(f"模型包成员长度或哈希格式无效：{path.name}")
            digest = hashlib.sha256()
            size = 0
            with archive.open(filename) as member:
                while chunk := member.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
            if size != expected_size or digest.hexdigest() != expected_hash:
                raise ValueError(f"模型包成员校验失败：{path.name} / {filename}")
    if manifest.get("format") != "partyops-modelpack" or manifest.get("format_version") != 2:
        raise ValueError(f"模型包格式不受支持：{path.name}")
    package_public = base64.b64decode(str(manifest.get("public_key", "")), validate=True)
    if package_public != trusted_public_raw:
        raise ValueError(f"模型包信任根不匹配：{path.name}")
    signature = base64.b64decode(str(manifest.get("signature", "")), validate=True)
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    try:
        Ed25519PublicKey.from_public_bytes(trusted_public_raw).verify(signature, _canonical(unsigned))
    except InvalidSignature as exc:
        raise ValueError(f"模型包签名无效：{path.name}") from exc

    components = manifest.get("components")
    capabilities = sorted(components) if isinstance(components, dict) else []
    if not capabilities:
        raise ValueError(f"模型包没有可用能力：{path.name}")
    return {
        "model_id": str(manifest["model_id"]),
        "name": str(manifest["name"]),
        "version": str(manifest["version"]),
        "capabilities": capabilities,
        "platforms": list(manifest.get("platforms", [])),
        "architectures": list(manifest.get("architectures", [])),
        "runtime": str(manifest.get("runtime", "")),
        "resource_profile": dict(manifest.get("resource_profile", {})),
        "min_runtime_version": str(manifest.get("min_runtime_version", "")),
        "license_name": str(manifest.get("license_name", "")),
        "model_source": str(manifest.get("model_source", "")),
        "filename": path.name,
        "length": path.stat().st_size,
        "sha256": _sha256(path),
        "package_signature": str(manifest["signature"]),
    }


def _validated_asset_url(value: str, filename: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or "/downloads/" not in parsed.path
        or unquote(PurePosixPath(parsed.path).name) != filename
    ):
        raise ValueError(f"模型地址必须是指向 /downloads/{filename} 的标准 HTTPS 地址")
    return value


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 PartyOps 官网签名模型目录")
    parser.add_argument("--pack", type=Path, action="append", required=True, help="最终 .partyops-modelpack，可重复")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--base-url", help="共用公网模型目录，例如 https://example/downloads/models/")
    parser.add_argument(
        "--asset-url",
        action="append",
        default=[],
        help="单个冻结模型包的公网地址，格式 文件名=https://...；可重复并优先于共用基址",
    )
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--generated-at", required=True, help="北京时间 ISO 8601，必须由发布记录显式传入")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _args()
    if not args.base_url and not args.asset_url:
        raise SystemExit("必须提供 --base-url 或每个模型的 --asset-url")
    if args.base_url:
        parsed = urlparse(args.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/downloads")
        ):
            raise SystemExit("模型下载基址必须是无凭据、无参数且以 /downloads 结尾的 HTTPS 地址")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", args.release_version):
        raise SystemExit("发布版本格式无效")
    key = _private_key(args.private_key)
    public_raw = base64.b64decode(args.public_key.read_text(encoding="ascii").strip(), validate=True)
    if _public_raw(key) != public_raw:
        raise SystemExit("模型目录私钥与客户端信任根不匹配")

    models = [_read_pack(path.resolve(), public_raw) for path in args.pack]
    model_ids = [str(item["model_id"]) for item in models]
    if len(model_ids) != len(set(model_ids)):
        raise SystemExit("模型目录存在重复 model_id")
    asset_urls: dict[str, str] = {}
    for value in args.asset_url:
        filename, separator, url = value.partition("=")
        if not separator or not filename:
            raise SystemExit(f"模型公网地址格式无效：{value}")
        try:
            url = _validated_asset_url(url, filename)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if filename in asset_urls:
            raise SystemExit(f"模型公网地址重复：{filename}")
        asset_urls[filename] = url
    base_url = args.base_url.rstrip("/") + "/" if args.base_url else ""
    for item in models:
        filename = str(item["filename"])
        if filename in asset_urls:
            item["download_url"] = asset_urls[filename]
        elif base_url:
            item["download_url"] = base_url + quote(filename)
        else:
            raise SystemExit(f"模型缺少公网地址：{filename}")

    catalog: dict[str, object] = {
        "$schema": "https://www.partyops.cn/releases/model-catalog-v1.schema.json",
        "format": "partyops-model-catalog",
        "format_version": 1,
        "release_version": args.release_version,
        "generated_at": args.generated_at,
        "public_key_fingerprint": hashlib.sha256(public_raw).hexdigest(),
        "models": sorted(models, key=lambda item: str(item["model_id"])),
    }
    catalog["signature"] = base64.b64encode(key.sign(_canonical(catalog))).decode("ascii")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"模型目录已生成：{args.output}")
    print(f"模型数量：{len(models)}")
    print(f"SHA-256：{_sha256(args.output)}")


if __name__ == "__main__":
    main()
