"""签名离线更新包的校验、分发和可回滚运行记录。"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import zipfile
import secrets
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..audit import emit_event, write_audit
from ..backups import SCHEMA_VERSION
from ..config import get_settings
from ..database import get_session
from ..device_versions import ensure_current_release
from ..models import Device, DeviceCommand, ReleaseHistory, UpdatePackage, UpdateRun, User, utcnow
from ..problems import ProblemException
from ..schemas import ReleaseHistoryOut, UpdateApplyRequest, UpdatePackageOut, UpdateRunOut
from ..security import require_admin
from ..enums import UpdateStatus

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - 生产依赖固定包含 cryptography
    Ed25519PublicKey = None  # type: ignore[assignment,misc]


router = APIRouter(tags=["updates"])
SUPPORTED_FORMAT_VERSION = 2
MIN_UPDATE_FREE_BYTES = 512 * 1024 * 1024


def _sha256_path(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(name) > 512:
        raise ProblemException(422, "UPDATE_PACKAGE_INVALID", "更新包路径无效", "更新包包含不安全的文件路径。")


def _version_tuple(value: object) -> tuple[int, int, int]:
    """只接受可比较的三段数字版本，避免把展示标签当成系统命令。"""

    raw = str(value or "").strip()
    parts = raw.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ProblemException(
            422,
            "UPDATE_VERSION_INVALID",
            "更新版本号无效",
            "更新包版本必须使用“主版本.次版本.修订号”格式。",
        )
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _ensure_free_space(path, required_bytes: int) -> None:
    free = shutil.disk_usage(path).free
    if free < required_bytes:
        required_gb = required_bytes / 1024**3
        free_gb = free / 1024**3
        raise ProblemException(
            507,
            "UPDATE_DISK_FULL",
            "升级空间不足",
            f"至少需要 {required_gb:.1f} GB 可用空间，当前约 {free_gb:.1f} GB。",
        )


def _validate_manifest_contract(manifest: dict) -> None:
    if manifest.get("format") != "partyops-update":
        raise ProblemException(
            422,
            "UPDATE_FORMAT_INVALID",
            "更新包类型无效",
            "请选择由党建智办发布工具生成的更新包。",
        )
    if int(manifest.get("format_version", 0)) != SUPPORTED_FORMAT_VERSION:
        raise ProblemException(
            422,
            "UPDATE_FORMAT_VERSION_UNSUPPORTED",
            "更新包格式版本不受支持",
            f"当前仅支持第 {SUPPORTED_FORMAT_VERSION} 版更新包格式。",
        )
    architecture_artifacts = manifest.get("architecture_artifacts")
    if not isinstance(architecture_artifacts, dict) or set(architecture_artifacts) != {
        "amd64",
        "arm64",
    }:
        raise ProblemException(
            422,
            "UPDATE_ARCHITECTURES_INCOMPLETE",
            "更新包缺少双架构安装程序",
            "更新包必须同时包含 amd64 和 ARM64 安装程序。",
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ProblemException(
            422,
            "UPDATE_MANIFEST_INVALID",
            "更新制品清单无效",
            "制品清单必须按文件名列出。",
        )
    for architecture, filename in architecture_artifacts.items():
        if (
            not isinstance(filename, str)
            or not filename.endswith(f"_{architecture}.deb")
            or filename not in artifacts
        ):
            raise ProblemException(
                422,
                "UPDATE_ARCHITECTURE_ARTIFACT_INVALID",
                "架构安装程序不匹配",
                f"{architecture} 安装程序与清单不一致。",
            )
    platform_artifacts = manifest.get("platform_artifacts")
    if platform_artifacts is not None:
        if not isinstance(platform_artifacts, dict):
            raise ProblemException(422, "UPDATE_PLATFORM_ARTIFACTS_INVALID", "平台制品映射无效", "平台制品映射必须按系统和架构列出。")
        uos_artifacts = platform_artifacts.get("uos")
        windows_artifacts = platform_artifacts.get("windows")
        if not isinstance(uos_artifacts, dict) or uos_artifacts != architecture_artifacts:
            raise ProblemException(422, "UPDATE_UOS_ARTIFACTS_INVALID", "UOS 制品映射无效", "UOS 平台映射必须与旧双架构映射一致。")
        if not isinstance(windows_artifacts, dict) or set(windows_artifacts) != {"amd64"}:
            raise ProblemException(422, "UPDATE_WINDOWS_ARTIFACT_MISSING", "更新包缺少 Windows 安装器", "统一更新包必须包含 Windows x64 安装器。")
        windows_name = windows_artifacts["amd64"]
        if (
            not isinstance(windows_name, str)
            or not windows_name.endswith("_windows_amd64.exe")
            or windows_name not in artifacts
        ):
            raise ProblemException(422, "UPDATE_WINDOWS_ARTIFACT_INVALID", "Windows 安装器与清单不一致", "请重新生成统一更新包。")
    target = _version_tuple(manifest.get("version"))
    current = _version_tuple(__version__)
    minimum = _version_tuple(manifest.get("min_version"))
    if target <= current:
        raise ProblemException(
            409,
            "UPDATE_VERSION_NOT_NEWER",
            "更新包不是更高版本",
            f"当前版本为 {__version__}，请选择更高版本更新包。",
        )
    if current < minimum:
        raise ProblemException(
            409,
            "UPDATE_BRIDGE_REQUIRED",
            "需要先安装桥接版本",
            f"该更新包要求当前版本不低于 {manifest.get('min_version')}。",
        )
    schema_revision = str(manifest.get("schema_revision", ""))
    if not schema_revision.isdigit() or len(schema_revision) != 4:
        raise ProblemException(
            422,
            "UPDATE_SCHEMA_INVALID",
            "数据库模式版本无效",
            "更新包缺少有效的数据库模式版本。",
        )
    if schema_revision < SCHEMA_VERSION:
        raise ProblemException(
            409,
            "UPDATE_SCHEMA_DOWNGRADE",
            "更新包数据库模式过旧",
            "系统禁止数据库模式降级。",
        )
    release_notes = manifest.get("release_notes")
    if (
        not isinstance(release_notes, list)
        or not release_notes
        or len(release_notes) > 50
        or any(not isinstance(note, str) or not note.strip() or len(note) > 500 for note in release_notes)
    ):
        raise ProblemException(
            422,
            "UPDATE_RELEASE_NOTES_INVALID",
            "更新内容不完整",
            "更新包必须包含 1—50 条简明中文更新内容。",
        )


def _manifest_signature_valid(manifest: dict) -> bool:
    signature = manifest.get("signature")
    settings = get_settings()
    public_key = settings.update_public_key
    if not public_key and settings.environment != "production":
        public_key = manifest.get("public_key")
    if not signature or not public_key or Ed25519PublicKey is None:
        return False
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    try:
        key_bytes = base64.b64decode(public_key)
        signature_bytes = base64.b64decode(signature)
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature_bytes, canonical)
        return True
    except Exception:
        return False


def _extract_manifest(path):
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                _safe_zip_member(info.filename)
                if info.is_dir():
                    continue
                if info.filename.startswith("/") or (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ProblemException(422, "UPDATE_PACKAGE_INVALID", "更新包包含链接文件", "请重新获取官方更新包。")
            raw = archive.read("manifest.json")
            archive_names = set(archive.namelist())
    except KeyError as exc:
        raise ProblemException(422, "UPDATE_MANIFEST_MISSING", "更新包缺少清单", "请使用 .partyops-update 更新包。") from exc
    except zipfile.BadZipFile as exc:
        raise ProblemException(422, "UPDATE_PACKAGE_INVALID", "更新包损坏", "请重新复制更新包后重试。") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProblemException(422, "UPDATE_MANIFEST_INVALID", "更新清单无效", "请重新生成更新包。") from exc
    if not isinstance(manifest, dict) or not manifest.get("version") or not manifest.get("artifacts"):
        raise ProblemException(422, "UPDATE_MANIFEST_INVALID", "更新清单字段不完整", "清单必须包含版本和架构制品。")
    _validate_manifest_contract(manifest)
    artifacts = manifest["artifacts"]
    for filename, expected in artifacts.items():
        _safe_zip_member(str(filename))
        if filename not in archive_names or not isinstance(expected, dict):
            raise ProblemException(422, "UPDATE_ARTIFACT_MISSING", "更新制品缺失", f"更新包缺少 {filename}。")
        with zipfile.ZipFile(path) as archive:
            digest = hashlib.sha256()
            size = 0
            with archive.open(filename) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
        if int(expected.get("size", -1)) != size or str(expected.get("sha256", "")).lower() != digest.hexdigest():
            raise ProblemException(422, "UPDATE_ARTIFACT_HASH_MISMATCH", "更新制品校验失败", f"{filename} 的大小或哈希不匹配。")
    return manifest


@router.get("/admin/updates", response_model=list[UpdatePackageOut])
def list_update_packages(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdatePackage]:
    return list(db.scalars(select(UpdatePackage).order_by(UpdatePackage.created_at.desc())).all())


@router.get("/admin/update-history", response_model=list[ReleaseHistoryOut])
def list_update_history(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[ReleaseHistory]:
    ensure_current_release(db)
    db.commit()
    return list(
        db.scalars(
            select(ReleaseHistory).order_by(ReleaseHistory.installed_at.desc())
        ).all()
    )


@router.post("/admin/updates/upload", response_model=UpdatePackageOut, status_code=201)
async def upload_update_package(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> UpdatePackage:
    settings = get_settings()
    _ensure_free_space(settings.updates_dir, MIN_UPDATE_FREE_BYTES)
    safe_name = PurePosixPath(file.filename or "partyops.partyops-update").name
    if not safe_name.endswith(".partyops-update"):
        raise ProblemException(422, "UPDATE_EXTENSION_INVALID", "更新包格式不正确", "请选择 .partyops-update 文件。")
    path = settings.updates_dir / f"upload-{secrets.token_hex(8)}.partyops-update"
    total_size = 0
    with path.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > 4 * 1024**3:
                path.unlink(missing_ok=True)
                raise ProblemException(413, "UPDATE_PACKAGE_TOO_LARGE", "更新包超过4GB限制", "请重新生成更新包。")
            handle.write(chunk)
    try:
        manifest = _extract_manifest(path)
        _ensure_free_space(
            settings.updates_dir,
            max(MIN_UPDATE_FREE_BYTES, total_size * 3),
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    signature_valid = _manifest_signature_valid(manifest)
    if settings.environment == "production" and not signature_valid:
        path.unlink(missing_ok=True)
        raise ProblemException(422, "UPDATE_SIGNATURE_INVALID", "更新包签名无效", "生产环境只接受经批准密钥签名的更新包。")
    digest = _sha256_path(path)
    final_name = f"partyops_{manifest['version']}_{digest[:12]}.partyops-update"
    final_path = settings.updates_dir / final_name
    path.replace(final_path)
    package = UpdatePackage(
        filename=final_name,
        version=str(manifest["version"]),
        min_version=str(manifest.get("min_version", "")),
        schema_revision=str(manifest.get("schema_revision", "")),
        manifest={key: value for key, value in manifest.items() if key not in {"signature", "public_key"}},
        sha256=digest,
        signature_valid=signature_valid,
        status=UpdateStatus.VALIDATED,
        created_by=admin.id,
    )
    db.add(package)
    write_audit(db, admin, "update.upload", "update_package", package.id, {"version": package.version, "signature_valid": signature_valid}, request.client.host if request.client else "")
    db.commit()
    db.refresh(package)
    return package


@router.post("/admin/updates/{package_id}/apply", response_model=list[UpdateRunOut], status_code=202)
def apply_update(
    package_id: str,
    payload: UpdateApplyRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdateRun]:
    package = db.get(UpdatePackage, package_id)
    if not package or package.status not in {UpdateStatus.VALIDATED, UpdateStatus.COMPLETED}:
        raise ProblemException(409, "UPDATE_NOT_READY", "更新包尚未通过校验", "请先上传并校验更新包。")
    if not package.signature_valid and get_settings().environment == "production":
        raise ProblemException(403, "UPDATE_SIGNATURE_INVALID", "更新包签名无效", "生产环境禁止使用未签名更新包。")
    package_path = get_settings().updates_dir / package.filename
    if not package_path.is_file() or _sha256_path(package_path) != package.sha256:
        raise ProblemException(
            410,
            "UPDATE_FILE_MISSING",
            "更新包文件缺失或校验失败",
            "请重新导入更新包。",
        )
    _ensure_free_space(
        get_settings().data_dir,
        max(MIN_UPDATE_FREE_BYTES, package_path.stat().st_size * 3),
    )
    active_host_run = db.scalar(
        select(UpdateRun).where(
            UpdateRun.target_device_id.is_(None),
            UpdateRun.status == UpdateStatus.APPLYING,
        )
    )
    if payload.include_host and active_host_run:
        raise ProblemException(
            409,
            "UPDATE_ALREADY_RUNNING",
            "主机已有升级任务正在运行",
            "请等待当前升级完成后再试。",
        )
    devices = []
    target_ids = payload.target_device_ids
    if payload.include_host:
        # 主机是唯一更新权威。主机升级时始终登记全部启用终端，
        # 离线终端保留等待记录，下一次上线后由用户确认更新。
        devices = list(
            db.scalars(
                select(Device).where(Device.active.is_(True)).order_by(Device.created_at)
            ).all()
        )
        target_ids = [device.id for device in devices]
    elif target_ids:
        devices = list(db.scalars(select(Device).where(Device.id.in_(target_ids), Device.active.is_(True))).all())
        if len(devices) != len(set(target_ids)):
            raise ProblemException(404, "DEVICE_NOT_FOUND", "目标设备不存在", "请刷新设备列表后重试。")
    runs: list[UpdateRun] = []
    for device in devices:
        run = db.scalar(
            select(UpdateRun).where(
                UpdateRun.package_id == package.id,
                UpdateRun.target_device_id == device.id,
                UpdateRun.status.in_(
                    [
                        UpdateStatus.UPLOADED,
                        UpdateStatus.APPLYING,
                        UpdateStatus.COMPLETED,
                    ]
                ),
            )
        )
        if run:
            runs.append(run)
            continue
        waiting_for_host = payload.include_host
        run = UpdateRun(
            package_id=package.id,
            target_device_id=device.id,
            status=UpdateStatus.UPLOADED,
            message=(
                "等待主机升级和健康检查完成"
                if waiting_for_host
                else "等待设备上线后升级"
            ),
            created_by=admin.id,
        )
        db.add(run)
        db.flush()
        if not waiting_for_host:
            db.add(
                DeviceCommand(
                    device_id=device.id,
                    command_type="apply_update",
                    idempotency_key=f"update:{package.id}:{device.id}",
                    payload={
                        "package": package.filename,
                        "version": package.version,
                        "run_id": run.id,
                    },
                )
            )
        runs.append(run)
    if payload.include_host:
        run = UpdateRun(
            package_id=package.id,
            target_device_id=None,
            status=UpdateStatus.APPLYING,
            progress=5,
            message="已进入主机升级队列；终端将在主机健康检查通过后升级。",
            created_by=admin.id,
        )
        db.add(run)
        db.flush()
        package.status = UpdateStatus.APPLYING
        runs.insert(0, run)
    if not runs:
        raise ProblemException(422, "UPDATE_TARGET_REQUIRED", "请选择升级目标", "至少选择主机或一台协同设备。")
    write_audit(db, admin, "update.apply", "update_package", package.id, {"targets": target_ids, "host": payload.include_host}, request.client.host if request.client else "")
    emit_event(db, "update.queued", package.id, {"target_count": len(devices), "include_host": payload.include_host})
    db.commit()
    return runs


@router.get("/admin/update-runs", response_model=list[UpdateRunOut])
def list_update_runs(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[UpdateRun]:
    return list(db.scalars(select(UpdateRun).order_by(UpdateRun.created_at.desc()).limit(200)).all())


@router.get("/devices/update-package/{filename}")
def download_device_update(
    filename: str,
    token: str | None = Header(default=None, alias="X-PartyOps-Device-Token"),
    db: Session = Depends(get_session),
) -> FileResponse:
    from .fleet import authenticated_device

    device = authenticated_device(token, db)
    package = db.scalar(
        select(UpdatePackage).where(
            UpdatePackage.filename == filename,
            UpdatePackage.status.in_(
                [
                    UpdateStatus.VALIDATED,
                    UpdateStatus.APPLYING,
                    UpdateStatus.COMPLETED,
                ]
            ),
        )
    )
    if not package:
        raise ProblemException(404, "UPDATE_NOT_FOUND", "更新包不存在", "更新任务可能已撤销。")
    path = get_settings().updates_dir / package.filename
    if not path.exists():
        raise ProblemException(410, "UPDATE_FILE_MISSING", "更新包文件缺失", "请由管理员重新上传更新包。")
    return FileResponse(path, media_type="application/octet-stream", filename=package.filename)
