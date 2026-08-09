#!/usr/bin/env python3
"""从已审核的离线模型文件生成签名 PartyOps 模型包。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_private_key(path: Path) -> Ed25519PrivateKey:
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except ValueError:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(data.strip()))
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("模型包签名必须使用 Ed25519 私钥")
    return key


def safe_basename(path: Path) -> str:
    name = PurePosixPath(path.name).name
    if not name or name in {".", ".."} or "\\" in name:
        raise SystemExit(f"文件名不安全：{path}")
    return name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建签名 .partyops-modelpack")
    parser.add_argument("--llm", type=Path, help="Qwen GGUF 模型")
    parser.add_argument("--embedding", type=Path, help="BGE ONNX 模型")
    parser.add_argument("--tokenizer", type=Path, help="BGE tokenizer.json（向量包必填）")
    parser.add_argument("--license", type=Path, action="append", required=True, help="模型许可文件，可重复")
    parser.add_argument("--private-key", type=Path, required=True, help="Ed25519 发布私钥")
    parser.add_argument("--architecture", choices=["universal", "amd64", "arm64"], default="universal")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--model-id", default="qwen3-1.7b-q8_0")
    parser.add_argument("--name", default="PartyOps 中文本地智能模型")
    parser.add_argument("--min-runtime-version", default="1.4.1")
    parser.add_argument("--estimated-memory-mb", type=int, default=0)
    parser.add_argument("--model-source", required=True, help="官方模型来源 URL 或离线来源说明")
    parser.add_argument("--license-name", required=True, help="例如 MIT 或 Apache-2.0")
    parser.add_argument("--pooling", choices=["cls", "mean"], default="cls")
    parser.add_argument("--query-prefix", default="为这个句子生成表示以用于检索相关文章：")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--dimension", type=int, default=512)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.llm and not args.embedding:
        raise SystemExit("必须至少提供 --embedding 或 --llm")
    if args.embedding and not args.tokenizer:
        raise SystemExit("中文向量包必须同时提供 --tokenizer")
    if args.tokenizer and not args.embedding:
        raise SystemExit("--tokenizer 只能与 --embedding 一起使用")
    if args.estimated_memory_mb < 0:
        raise SystemExit("--estimated-memory-mb 不能为负数")
    if not 8 <= args.max_length <= 512 or args.dimension <= 0:
        raise SystemExit("向量最大长度必须为 8—512，维度必须大于 0")
    inputs = [
        *([args.llm] if args.llm else []),
        *([args.embedding, args.tokenizer] if args.embedding else []),
        *args.license,
        args.private_key,
    ]
    for path in inputs:
        if not path.is_file():
            raise SystemExit(f"输入文件不存在：{path}")
    if args.output.suffix != ".partyops-modelpack":
        raise SystemExit("输出文件必须使用 .partyops-modelpack 扩展名")

    entries: list[tuple[Path, str]] = []
    components: dict[str, dict[str, object]] = {}
    if args.llm:
        llm_name = f"models/llm/{safe_basename(args.llm)}"
        entries.append((args.llm, llm_name))
        components["llm"] = {"model_file": llm_name, "context_size": 4096}
    if args.embedding and args.tokenizer:
        embedding_name = f"models/embedding/{safe_basename(args.embedding)}"
        tokenizer_name = f"models/embedding/{safe_basename(args.tokenizer)}"
        entries.extend([(args.embedding, embedding_name), (args.tokenizer, tokenizer_name)])
        components["embedding"] = {
            "model_file": embedding_name,
            "tokenizer_file": tokenizer_name,
            "model_id": "BAAI/bge-small-zh-v1.5",
            "pooling": args.pooling,
            "query_prefix": args.query_prefix,
            "max_length": args.max_length,
            "dimension": args.dimension,
        }
    used_names: set[str] = {item[1] for item in entries}
    license_names: list[str] = []
    for index, path in enumerate(args.license, start=1):
        name = f"licenses/{index:02d}-{safe_basename(path)}"
        if name in used_names:
            raise SystemExit(f"模型包内部文件名重复：{name}")
        used_names.add(name)
        license_names.append(name)
        entries.append((path, name))

    files = {
        name: {"sha256": sha256_file(path), "size": path.stat().st_size}
        for path, name in entries
    }
    manifest = {
        "format": "partyops-modelpack",
        "format_version": 1,
        "name": args.name,
        "version": args.version,
        "model_id": args.model_id,
        "architecture": args.architecture,
        "min_runtime_version": args.min_runtime_version,
        "estimated_memory_mb": args.estimated_memory_mb,
        "model_source": args.model_source,
        "license_name": args.license_name,
        "components": components,
        "license_files": license_names,
        "files": files,
        "public_key": "",
        "signature": "",
    }
    key = load_private_key(args.private_key)
    manifest["public_key"] = base64.b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["signature"] = base64.b64encode(key.sign(canonical)).decode("ascii")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", allowZip64=True) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        for path, name in entries:
            compression = zipfile.ZIP_STORED if path.stat().st_size >= 16 * 1024**2 else zipfile.ZIP_DEFLATED
            archive.write(path, name, compress_type=compression)
    print(f"模型包已生成：{args.output}")
    print(f"SHA-256：{sha256_file(args.output)}")


if __name__ == "__main__":
    main()
