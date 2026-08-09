"""PartyOps 本地模型包的签名、哈希、路径和安装校验。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .enums import ModelPackStatus
from .models import AIModelActivation, AIModelPack, User, utcnow
from .problems import ProblemException

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


MODEL_PACK_FORMAT_VERSION = 1
MAX_MODEL_PACK_BYTES = 4 * 1024**3
MAX_UNPACKED_BYTES = 6 * 1024**3
MAX_MEMBERS = 64

# 激活时会执行完整 SHA-256 校验。运行状态页需要频繁读取模型状态，不能
# 每次都重新哈希约 2GB 的模型文件，因此缓存“路径、大小、修改时间”指纹；
# 任一文件发生变化后才重新执行完整校验。缓存只存在于当前主机进程内。
_verification_cache: dict[str, tuple[tuple[str, int, int], ...]] = {}
_verification_lock = threading.RLock()


def normalized_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine or "unknown"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    value = PurePosixPath(name)
    if (
        value.is_absolute()
        or ".." in value.parts
        or not value.parts
        or len(name) > 512
        or "\\" in name
    ):
        raise ProblemException(422, "MODEL_PACK_PATH_INVALID", "模型包路径无效", "模型包包含不安全路径。")
    return value


def _manifest_signature_valid(manifest: dict) -> bool:
    settings = get_settings()
    public_key = settings.model_pack_public_key or settings.update_public_key
    if not public_key and settings.environment != "production":
        public_key = str(manifest.get("public_key", ""))
    signature = str(manifest.get("signature", ""))
    if not public_key or not signature or Ed25519PublicKey is None:
        return False
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
            base64.b64decode(signature), canonical
        )
        return True
    except Exception:
        return False


def _validate_manifest(manifest: dict, archive: zipfile.ZipFile) -> tuple[dict, bool]:
    if manifest.get("format") != "partyops-modelpack" or int(manifest.get("format_version", 0)) != MODEL_PACK_FORMAT_VERSION:
        raise ProblemException(422, "MODEL_PACK_FORMAT_INVALID", "模型包格式无效", "请选择 PartyOps 兼容的 .partyops-modelpack 文件。")
    files = manifest.get("files")
    components = manifest.get("components")
    if not isinstance(files, dict) or not isinstance(components, dict):
        raise ProblemException(422, "MODEL_PACK_MANIFEST_INVALID", "模型包清单不完整", "模型包缺少文件或组件清单。")
    try:
        estimated_memory_mb = int(manifest.get("estimated_memory_mb", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ProblemException(422, "MODEL_PACK_MEMORY_INVALID", "模型内存需求无效", "estimated_memory_mb 必须是非负整数。") from exc
    if not 0 <= estimated_memory_mb <= 1024 * 1024:
        raise ProblemException(422, "MODEL_PACK_MEMORY_INVALID", "模型内存需求无效", "estimated_memory_mb 超出合理范围。")
    llm = components.get("llm")
    embedding = components.get("embedding")
    capabilities: list[str] = []
    required: set[str] = set()
    if isinstance(llm, dict):
        capabilities.append("llm")
        required.add(str(llm.get("model_file", "")))
    if isinstance(embedding, dict):
        capabilities.append("embedding")
        required.update(
            {
                str(embedding.get("model_file", "")),
                str(embedding.get("tokenizer_file", "")),
            }
        )
        pooling = str(embedding.get("pooling", "cls")).lower()
        if pooling not in {"cls", "mean"}:
            raise ProblemException(422, "MODEL_PACK_POOLING_INVALID", "向量池化配置无效", "pooling 只支持 cls 或 mean。")
        for field, default, lower, upper in (
            ("max_length", 512, 1, 8192),
            ("dimension", 512, 1, 65536),
        ):
            try:
                value = int(embedding.get(field, default))
            except (TypeError, ValueError) as exc:
                raise ProblemException(422, "MODEL_PACK_EMBEDDING_INVALID", "向量模型配置无效", f"{field} 必须是整数。") from exc
            if not lower <= value <= upper:
                raise ProblemException(422, "MODEL_PACK_EMBEDDING_INVALID", "向量模型配置无效", f"{field} 超出允许范围。")
    if not capabilities:
        raise ProblemException(
            422,
            "MODEL_PACK_COMPONENT_MISSING",
            "模型组件不完整",
            "模型包必须至少包含 embedding 或 llm 组件。",
        )
    licenses = manifest.get("license_files", [])
    if not isinstance(licenses, list) or not licenses:
        raise ProblemException(422, "MODEL_PACK_LICENSE_MISSING", "模型许可文件缺失", "模型包必须包含模型许可说明。")
    required.update(str(item) for item in licenses)
    if "" in required or not required.issubset(set(files)):
        raise ProblemException(422, "MODEL_PACK_FILE_MISSING", "模型文件缺失", "模型包文件与清单不一致。")
    names = set(archive.namelist())
    total_unpacked = 0
    if len(names) > MAX_MEMBERS:
        raise ProblemException(422, "MODEL_PACK_TOO_MANY_FILES", "模型包文件过多", "模型包结构不符合发布规范。")
    for info in archive.infolist():
        _safe_member(info.filename)
        if info.is_dir():
            continue
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ProblemException(422, "MODEL_PACK_LINK_DENIED", "模型包包含链接文件", "模型包不允许包含符号链接。")
        total_unpacked += info.file_size
    if total_unpacked > MAX_UNPACKED_BYTES:
        raise ProblemException(413, "MODEL_PACK_TOO_LARGE", "模型包解压后过大", "模型包超过6GB解压限制。")
    for filename, expected in files.items():
        _safe_member(str(filename))
        if filename not in names or not isinstance(expected, dict):
            raise ProblemException(422, "MODEL_PACK_FILE_MISSING", "模型文件缺失", f"模型包缺少 {filename}。")
        info = archive.getinfo(filename)
        # 小型 JSON/许可文本天然具有较高压缩率；只对大成员执行炸弹判定，
        # 避免误伤正规分词器文件，同时保留总解压大小与成员数上限。
        if (
            info.file_size >= 10 * 1024**2
            and info.compress_size > 0
            and info.file_size / info.compress_size > 100
        ):
            raise ProblemException(422, "MODEL_PACK_RATIO_INVALID", "模型包压缩比例异常", "模型包可能已损坏。")
        digest = hashlib.sha256()
        size = 0
        with archive.open(filename) as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != int(expected.get("size", -1)) or digest.hexdigest() != str(expected.get("sha256", "")).lower():
            raise ProblemException(422, "MODEL_PACK_HASH_MISMATCH", "模型文件校验失败", f"{filename} 的大小或哈希不一致。")
    signature_valid = _manifest_signature_valid(manifest)
    if get_settings().environment == "production" and not signature_valid:
        raise ProblemException(422, "MODEL_PACK_SIGNATURE_INVALID", "模型包签名无效", "生产环境只接受经 PartyOps 发布密钥签名的模型包。")
    return files, signature_valid


def _numeric_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*", value)
    if not match:
        raise ProblemException(422, "MODEL_RUNTIME_VERSION_INVALID", "模型运行时版本无效", "min_runtime_version 必须是三段数字版本。")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def install_model_pack(path: Path, original_name: str, admin: User, db: Session) -> AIModelPack:
    if path.stat().st_size > MAX_MODEL_PACK_BYTES:
        raise ProblemException(413, "MODEL_PACK_TOO_LARGE", "模型包超过4GB限制", "请重新获取精简模型包。")
    digest = sha256_path(path)
    existing = db.scalar(select(AIModelPack).where(AIModelPack.sha256 == digest))
    if existing:
        raise ProblemException(
            409,
            "MODEL_PACK_ALREADY_INSTALLED",
            "模型包已经安装",
            f"当前已存在版本 {existing.version}，无需重复导入。",
        )
    install_root: Path | None = None
    stored_path: Path | None = None
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProblemException(422, "MODEL_PACK_MANIFEST_INVALID", "模型包清单无效", "模型包缺少有效 manifest.json。") from exc
            if not isinstance(manifest, dict):
                raise ProblemException(422, "MODEL_PACK_MANIFEST_INVALID", "模型包清单无效", "模型包清单必须是对象。")
            files, signature_valid = _validate_manifest(manifest, archive)
            install_key = secrets.token_hex(16)
            settings = get_settings()
            free = shutil.disk_usage(settings.models_dir).free
            required_free = max(3 * 1024**3, path.stat().st_size * 2)
            if free < required_free:
                raise ProblemException(507, "MODEL_PACK_DISK_FULL", "模型安装空间不足", "请至少保留3GB可用空间后重试。")
            with tempfile.TemporaryDirectory(prefix="modelpack-", dir=settings.models_dir) as temporary:
                temporary_root = Path(temporary)
                for filename in files:
                    relative = _safe_member(str(filename))
                    target = temporary_root.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(filename) as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination, length=1024 * 1024)
                manifest_copy = {key: value for key, value in manifest.items() if key not in {"signature", "public_key"}}
                (temporary_root / "manifest.json").write_text(
                    json.dumps(manifest_copy, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                install_root = settings.models_dir / install_key
                os.replace(temporary_root, install_root)
        # Windows 会在 ZipFile 仍打开时锁住源包；必须先退出上下文，再把
        # 已校验原包原子移入受管目录。UOS/Linux 也沿用相同顺序。
        packages_dir = settings.models_dir / "packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        safe_original = PurePosixPath(original_name or "partyops.partyops-modelpack").name
        stored_name = f"{Path(safe_original).stem}-{digest[:12]}.partyops-modelpack"
        stored_path = packages_dir / stored_name
        os.replace(path, stored_path)
    except zipfile.BadZipFile as exc:
        raise ProblemException(422, "MODEL_PACK_INVALID", "模型包损坏", "请重新复制模型包后再导入。") from exc
    except Exception:
        if install_root and install_root.parent == get_settings().models_dir and install_root.exists():
            shutil.rmtree(install_root)
        if stored_path and stored_path.parent == get_settings().models_dir / "packages":
            stored_path.unlink(missing_ok=True)
        raise
    record = AIModelPack(
        name=str(manifest.get("name", "PartyOps 本地智能模型"))[:160],
        version=str(manifest.get("version", "1.0.0"))[:32],
        model_id=str(manifest.get("model_id", "qwen3-1.7b-q8_0"))[:160],
        architecture=str(manifest.get("architecture", "universal"))[:16],
        filename=stored_name,
        install_key=install_key,
        sha256=digest,
        size_bytes=stored_path.stat().st_size,
        manifest=manifest_copy,
        capabilities=[
            capability
            for capability in ("embedding", "llm")
            if isinstance(manifest_copy.get("components", {}).get(capability), dict)
        ],
        min_runtime_version=str(manifest.get("min_runtime_version", "1.4.1"))[:32],
        estimated_memory_mb=max(0, int(manifest.get("estimated_memory_mb", 0) or 0)),
        model_source=str(manifest.get("model_source", ""))[:500],
        license_name=str(manifest.get("license_name", ""))[:80],
        signature_valid=signature_valid,
        status=ModelPackStatus.INSTALLED,
        created_by=admin.id,
    )
    db.add(record)
    db.flush()
    return record


def model_pack_root(pack: AIModelPack) -> Path:
    root = (get_settings().models_dir / pack.install_key).resolve()
    if root.parent != get_settings().models_dir.resolve():
        raise ProblemException(500, "MODEL_PACK_PATH_INVALID", "模型安装记录异常", "请重新导入模型包。")
    return root


def remove_installed_pack_files(pack: AIModelPack) -> None:
    """仅用于数据库事务未提交时撤销刚完成的模型文件安装。"""

    root = model_pack_root(pack)
    if root.exists():
        shutil.rmtree(root)
    package = (get_settings().models_dir / "packages" / pack.filename).resolve()
    packages_root = (get_settings().models_dir / "packages").resolve()
    if package.parent == packages_root:
        package.unlink(missing_ok=True)


def verify_installed_pack(pack: AIModelPack) -> bool:
    try:
        root = model_pack_root(pack)
        files = pack.manifest.get("files", {})
        if not isinstance(files, dict) or not root.is_dir():
            with _verification_lock:
                _verification_cache.pop(pack.id, None)
            return False
        fingerprint: list[tuple[str, int, int]] = []
        for filename, expected in files.items():
            relative = _safe_member(str(filename))
            path = root.joinpath(*relative.parts)
            if not path.is_file():
                with _verification_lock:
                    _verification_cache.pop(pack.id, None)
                return False
            stat = path.stat()
            if stat.st_size != int(expected.get("size", -1)):
                with _verification_lock:
                    _verification_cache.pop(pack.id, None)
                return False
            fingerprint.append((str(relative), stat.st_size, stat.st_mtime_ns))
        cache_key = tuple(sorted(fingerprint))
        with _verification_lock:
            if _verification_cache.get(pack.id) == cache_key:
                return True
        for filename, expected in files.items():
            relative = _safe_member(str(filename))
            path = root.joinpath(*relative.parts)
            if sha256_path(path) != str(expected.get("sha256", "")).lower():
                with _verification_lock:
                    _verification_cache.pop(pack.id, None)
                return False
        with _verification_lock:
            _verification_cache[pack.id] = cache_key
        return True
    except (OSError, TypeError, ValueError, ProblemException):
        # 文件被外部收回权限、占用或损坏时，状态接口必须降级为“模型不可用”，
        # 不能让设置页、系统诊断和后台索引任务一起返回 500。
        with _verification_lock:
            _verification_cache.pop(pack.id, None)
        return False


def active_model_pack(db: Session, capability: str = "embedding") -> AIModelPack | None:
    if capability not in {"embedding", "llm"}:
        return None
    activation = db.scalar(
        select(AIModelActivation).where(AIModelActivation.capability == capability)
    )
    if activation:
        pack = db.get(AIModelPack, activation.model_pack_id)
        if pack and capability in (pack.capabilities or []):
            return pack
    # 兼容 1.4.0 组合模型：升级后尚未建立分能力记录时，旧 active 状态仍可用。
    legacy = db.scalar(
        select(AIModelPack).where(AIModelPack.status == ModelPackStatus.ACTIVE)
    )
    if legacy and (
        capability in (legacy.capabilities or [])
        or isinstance((legacy.manifest or {}).get("components", {}).get(capability), dict)
    ):
        return legacy
    return None


def activate_model_pack(
    db: Session,
    pack: AIModelPack,
    capability: str,
    activated_by: str,
) -> AIModelPack:
    if capability not in {"embedding", "llm"}:
        raise ProblemException(422, "MODEL_CAPABILITY_INVALID", "模型能力无效", "请选择 embedding 或 llm。")
    if capability not in (pack.capabilities or []):
        raise ProblemException(422, "MODEL_CAPABILITY_MISSING", "模型包不含所选能力", "请选择包含该组件的模型包。")
    if _numeric_version(pack.min_runtime_version or "1.4.1") > _numeric_version(get_settings().app_version):
        raise ProblemException(
            409,
            "MODEL_RUNTIME_TOO_OLD",
            "当前程序版本无法运行该模型",
            f"请先升级 PartyOps 至 {pack.min_runtime_version} 或更高版本。",
        )
    if not verify_installed_pack(pack):
        pack.status = ModelPackStatus.CORRUPT
        db.flush()
        raise ProblemException(409, "MODEL_PACK_CORRUPT", "模型包校验失败", "请重新导入模型包。")
    activation = db.scalar(
        select(AIModelActivation).where(AIModelActivation.capability == capability)
    )
    previous_pack_id = activation.model_pack_id if activation else None
    if activation is None:
        activation = AIModelActivation(
            capability=capability,
            model_pack_id=pack.id,
            activated_by=activated_by,
        )
        db.add(activation)
    else:
        activation.model_pack_id = pack.id
        activation.activated_by = activated_by
        activation.activated_at = utcnow()
    pack.status = ModelPackStatus.ACTIVE
    pack.activated_at = utcnow()
    if previous_pack_id and previous_pack_id != pack.id:
        previous = db.get(AIModelPack, previous_pack_id)
        still_active = db.scalar(
            select(AIModelActivation.id).where(
                AIModelActivation.model_pack_id == previous_pack_id,
                AIModelActivation.capability != capability,
            )
        )
        if previous and not still_active:
            previous.status = ModelPackStatus.INSTALLED
            previous.activated_at = None
    db.flush()
    return pack


def deactivate_model_capability(db: Session, capability: str) -> AIModelPack | None:
    activation = db.scalar(
        select(AIModelActivation).where(AIModelActivation.capability == capability)
    )
    if not activation:
        return None
    pack = db.get(AIModelPack, activation.model_pack_id)
    db.delete(activation)
    db.flush()
    if pack and not db.scalar(
        select(AIModelActivation.id).where(AIModelActivation.model_pack_id == pack.id)
    ):
        pack.status = ModelPackStatus.INSTALLED
        pack.activated_at = None
    db.flush()
    return pack
