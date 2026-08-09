"""主机端受限更新执行器。

该模块只接受数据库中已通过签名校验的更新记录，按照固定架构选择内含
Debian 制品，不执行更新包中的脚本或任意路径；系统级安装由随包的 root
systemd 服务调用，业务进程本身不获得 root 权限。
"""

from __future__ import annotations

import hashlib
import json
import os
import base64
import logging
import platform
import shlex
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import ssl
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .config import get_settings
from .database import db_runtime
from .models import DeviceCommand, UpdatePackage, UpdateRun, utcnow
from .enums import UpdateStatus

logger = logging.getLogger(__name__)

# 更新锁必须能够跨进程阻止重复安装，同时也要能在断电、强制结束进程后
# 自动恢复。旧版本创建的是空锁文件，因此为它保留一个短暂保护期，超过
# 保护期即可安全接管；新锁记录进程和系统启动标识，能准确区分活锁与残锁。
LEGACY_UPDATE_LOCK_GRACE_SECONDS = 300


def _safe_member(name: str) -> None:
    if "\\" in name or "\x00" in name:
        raise RuntimeError("更新包包含非法路径")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or len(name) > 512:
        raise RuntimeError("更新包包含非法路径")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _system_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) 在 Windows 并不具备与 POSIX 完全相同的探测语义，
        # 使用只读进程句柄避免测试或维护工具误伤目标进程。
        try:
            import ctypes

            query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                query_limited_information,
                False,
                pid,
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        except (AttributeError, OSError):
            return pid == os.getpid()
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 更新监督器以 root 运行；即使在受限测试环境中无法探测，也不能
        # 冒险删除一个可能仍在执行 dpkg 的活锁。
        return True
    except OSError:
        return False


def _update_lock_is_stale(lock_path: Path) -> bool:
    """判断更新锁是否由已经退出或上次开机的执行器遗留。"""

    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        modified_at = lock_path.stat().st_mtime
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not raw:
        return time.time() - modified_at >= LEGACY_UPDATE_LOCK_GRACE_SECONDS
    try:
        payload = json.loads(raw)
        pid = int(payload.get("pid", 0))
        boot_id = str(payload.get("boot_id", ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return time.time() - modified_at >= LEGACY_UPDATE_LOCK_GRACE_SECONDS
    current_boot_id = _system_boot_id()
    if boot_id and current_boot_id and boot_id != current_boot_id:
        return True
    return not _process_is_running(pid)


def _acquire_update_lock(lock_path: Path) -> bool:
    """原子获取更新锁；发现残锁时仅接管一次，避免并发误删活锁。"""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            lock_fd = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            if not _update_lock_is_stale(lock_path):
                return False
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return False
            continue
        try:
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "boot_id": _system_boot_id(),
                    "created_at": time.time(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            os.write(lock_fd, payload)
            os.fsync(lock_fd)
            return True
        except OSError:
            lock_path.unlink(missing_ok=True)
            return False
        finally:
            os.close(lock_fd)
    return False


def _trusted_public_key() -> str:
    settings = get_settings()
    if settings.update_public_key:
        return settings.update_public_key.strip()
    candidates = [Path("/etc/partyops/update-public-key")]
    if os.name == "nt":
        candidates = [
            Path(sys.executable).resolve().parent / "update-public-key.txt",
            Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "update-public-key.txt",
        ]
    path = next((item for item in candidates if item.is_file()), None)
    return path.read_text(encoding="utf-8").strip() if path else ""


def _verify_manifest_signature(manifest: dict) -> bool:
    signature = str(manifest.get("signature", ""))
    public_key = _trusted_public_key()
    if not signature or not public_key:
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
            base64.b64decode(signature),
            canonical,
        )
        return True
    except Exception:
        return False


def _architecture() -> str:
    if os.name == "nt":
        machine = platform.machine().lower()
        if machine in {"amd64", "x86_64"}:
            return "amd64"
        raise RuntimeError("Windows 版本仅支持 x64 处理器")
    result = subprocess.run(
        ["dpkg", "--print-architecture"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    value = result.stdout.strip()
    if value not in {"amd64", "arm64"}:
        raise RuntimeError("当前系统架构不在 PartyOps 支持范围")
    return value


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _ensure_dpkg_ready() -> bool:
    """在覆盖程序前收敛上一次中断留下的 dpkg 半配置状态。"""

    result = _run(["dpkg", "--configure", "-a"], timeout=300)
    if result.returncode != 0:
        logger.error("dpkg_preflight_failed returncode=%s", result.returncode)
        return False
    return True


def _set_run(run_id: str, *, status: UpdateStatus, progress: int, message: str) -> None:
    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        if not run:
            return
        run.status = status
        run.progress = max(0, min(100, progress))
        run.message = message[:2_000]
        if status in {UpdateStatus.COMPLETED, UpdateStatus.FAILED, UpdateStatus.ROLLED_BACK}:
            run.completed_at = utcnow()
        db.commit()


def _select_artifact(
    package_path: Path,
    manifest: dict,
    architecture: str,
    target: Path,
    platform_name: str = "uos",
) -> Path:
    if not _verify_manifest_signature(manifest):
        raise RuntimeError("更新包发布签名无效")
    artifacts = manifest.get("artifacts", {})
    platform_artifacts = manifest.get("platform_artifacts", {})
    platform_map = (
        platform_artifacts.get(platform_name, {})
        if isinstance(platform_artifacts, dict)
        else {}
    )
    architecture_artifacts = manifest.get("architecture_artifacts", {})
    expected_name = str(platform_map.get(architecture, "")) if isinstance(platform_map, dict) else ""
    if not expected_name and platform_name == "uos":
        expected_name = (
            str(architecture_artifacts.get(architecture, ""))
            if isinstance(architecture_artifacts, dict)
            else ""
        )
    if not expected_name:
        raise RuntimeError(f"更新包不包含 {architecture} 安装制品")
    expected_suffix = ".exe" if platform_name == "windows" else f"_{architecture}.deb"
    if expected_name not in artifacts or not expected_name.endswith(expected_suffix):
        raise RuntimeError("架构安装制品与清单不一致")
    with zipfile.ZipFile(package_path) as archive:
        _safe_member(expected_name)
        info = archive.getinfo(expected_name)
        expected = artifacts.get(expected_name, {})
        if int(expected.get("size", -1)) != info.file_size:
            raise RuntimeError("安装制品大小与清单不一致")
        with archive.open(info) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
    if str(expected.get("sha256", "")).lower() != _hash(target):
        raise RuntimeError("安装制品哈希与清单不一致")
    return target


def _installed_package_version() -> str:
    result = _run(
        ["dpkg-query", "-W", "-f=${Status}\\t${Version}", "partyops"],
        timeout=20,
    )
    if result.returncode != 0:
        return ""
    status, _, version = result.stdout.strip().partition("\t")
    return version if status == "install ok installed" else ""


def _create_installed_package_snapshot(destination: Path) -> None:
    """把 dpkg 当前登记的 PartyOps 重建为可安装回滚包。

    这一步在停止服务和改动程序前完成。回滚时仍通过 dpkg 恢复程序版本，
    避免只复制 /opt 导致包管理器记录与实际程序不一致。
    """

    version = _installed_package_version()
    if not version:
        raise RuntimeError("无法读取当前 PartyOps 系统包版本")
    architecture = _architecture()
    listed = _run(["dpkg-query", "-L", "partyops"], timeout=30)
    if listed.returncode != 0:
        raise RuntimeError("无法读取当前 PartyOps 系统包文件清单")
    with tempfile.TemporaryDirectory(prefix="partyops-rollback-") as temporary:
        package_root = Path(temporary) / "package"
        control_root = package_root / "DEBIAN"
        control_root.mkdir(parents=True)
        for raw in listed.stdout.splitlines():
            source = Path(raw.strip())
            if (
                not source.is_absolute()
                or not source.exists()
                or source.is_dir()
                or source == Path("/var/lib/partyops")
                or Path("/var/lib/partyops") in source.parents
            ):
                continue
            target = package_root / source.relative_to("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            elif source.is_file():
                shutil.copy2(source, target, follow_symlinks=False)
        info_root = Path("/var/lib/dpkg/info")
        for suffix in ("preinst", "postinst", "prerm", "postrm", "config", "triggers"):
            source = info_root / f"partyops.{suffix}"
            if source.is_file():
                shutil.copy2(source, control_root / suffix)
        (control_root / "control").write_text(
            "\n".join(
                [
                    "Package: partyops",
                    f"Version: {version}",
                    "Section: office",
                    "Priority: optional",
                    f"Architecture: {architecture}",
                    "Maintainer: PartyOps",
                    "Description: 党建智办升级回滚快照",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = _run(
            ["dpkg-deb", "--build", str(package_root), str(destination)],
            timeout=180,
        )
        if result.returncode != 0 or not destination.is_file():
            raise RuntimeError("无法生成当前程序回滚包")


def _restore_managed_tree(backup: Path, destination: Path, data_root: Path) -> None:
    resolved_root = data_root.resolve(strict=False)
    resolved_destination = destination.resolve(strict=False)
    if (
        resolved_destination != resolved_root
        and resolved_root not in resolved_destination.parents
    ):
        raise RuntimeError("回滚目录超出 PartyOps 数据目录")
    if not backup.is_dir():
        return
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(backup, destination)


def _queue_device_updates(package: UpdatePackage, db) -> int:
    waiting = 0
    runs = list(
        db.scalars(
            select(UpdateRun).where(
                UpdateRun.package_id == package.id,
                UpdateRun.target_device_id.is_not(None),
                UpdateRun.status == UpdateStatus.UPLOADED,
            )
        ).all()
    )
    for run in runs:
        assert run.target_device_id is not None
        run.message = "主机升级完成，等待协同电脑用户确认更新"
        waiting += 1
    return waiting


def _online_backup_database(source: Path, destination: Path) -> None:
    """使用 SQLite backup API 获取一致快照，避免直接复制 WAL 中间态。"""

    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def _health_check() -> bool:
    settings = get_settings()
    scheme = "https" if settings.tls_enabled else "http"
    request = urllib.request.Request(
        f"{scheme}://{settings.host}:{settings.port}/api/v1/health"
    )
    try:
        context = None
        if settings.tls_enabled:
            material = settings.tls_client_ca_file
            if material and material.is_file():
                context = ssl.create_default_context(cafile=str(material))
        with urllib.request.urlopen(request, timeout=5, context=context) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError):
        return False


def _manifest_has_windows_artifact(manifest: dict, architecture: str = "amd64") -> bool:
    platforms = manifest.get("platform_artifacts", {})
    return bool(
        isinstance(platforms, dict)
        and isinstance(platforms.get("windows"), dict)
        and platforms["windows"].get(architecture)
    )


def _run_windows_installer(path: Path) -> bool:
    result = _run(
        [
            str(path),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
        ],
        timeout=900,
    )
    return result.returncode == 0


def _execute_windows_host_update(
    run_id: str,
    package_path: Path,
    manifest: dict,
) -> bool:
    """Windows 平台适配器：安装器升级、服务健康检查和安装器级回滚。"""

    settings = get_settings()
    lock_path = settings.data_dir / ".update.lock"
    if not _acquire_update_lock(lock_path):
        return False
    backup_root = settings.data_dir / "upgrade-backups" / run_id
    database_backup = backup_root / "partyops.db"
    attachments_backup = backup_root / "attachments"
    archives_backup = backup_root / "archives"
    installer_cache = settings.data_dir / "installer-cache"
    current_installer = installer_cache / "current.exe"
    rollback_installer = backup_root / "partyops-rollback.exe"
    try:
        _set_run(run_id, status=UpdateStatus.APPLYING, progress=10, message="正在创建 Windows 升级前快照")
        backup_root.mkdir(parents=True, exist_ok=True)
        installer_cache.mkdir(parents=True, exist_ok=True)
        if settings.database_path.exists():
            _online_backup_database(settings.database_path, database_backup)
        if current_installer.is_file():
            shutil.copy2(current_installer, rollback_installer)
        if settings.attachments_dir.is_dir():
            shutil.copytree(settings.attachments_dir, attachments_backup, dirs_exist_ok=True)
        if settings.archives_dir.is_dir():
            shutil.copytree(settings.archives_dir, archives_backup, dirs_exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="partyops-update-", dir=settings.transfers_dir) as temporary:
            artifact = _select_artifact(
                package_path,
                manifest,
                "amd64",
                Path(temporary) / "PartyOps-update.exe",
                "windows",
            )
            _set_run(run_id, status=UpdateStatus.APPLYING, progress=35, message="正在停止 Windows 主机服务并安装更新")
            _run(["sc.exe", "stop", "PartyOpsHost"], timeout=60)
            if not _run_windows_installer(artifact):
                raise RuntimeError("Windows 安装器拒绝更新制品")
            shutil.copy2(artifact, current_installer)
        _run(["sc.exe", "start", "PartyOpsHost"], timeout=60)
        _set_run(run_id, status=UpdateStatus.APPLYING, progress=80, message="正在执行数据库迁移和健康检查")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not _health_check():
            time.sleep(2)
        if not _health_check():
            raise RuntimeError("Windows 升级后健康检查未通过")
        with db_runtime.session_factory() as db:
            run = db.get(UpdateRun, run_id)
            package = db.get(UpdatePackage, run.package_id) if run else None
            if run:
                run.status = UpdateStatus.COMPLETED
                run.progress = 100
                run.message = "Windows 主机升级完成"
                run.completed_at = utcnow()
            if package:
                package.status = UpdateStatus.COMPLETED
                _queue_device_updates(package, db)
            db.commit()
        return True
    except Exception:
        logger.exception("Windows 主机升级失败，诊断编号=%s", run_id)
        _run(["sc.exe", "stop", "PartyOpsHost"], timeout=60)
        program_restored = rollback_installer.is_file() and _run_windows_installer(rollback_installer)
        if database_backup.exists():
            Path(f"{settings.database_path}-wal").unlink(missing_ok=True)
            Path(f"{settings.database_path}-shm").unlink(missing_ok=True)
            shutil.copy2(database_backup, settings.database_path)
        _restore_managed_tree(attachments_backup, settings.attachments_dir, settings.data_dir)
        _restore_managed_tree(archives_backup, settings.archives_dir, settings.data_dir)
        if program_restored:
            shutil.copy2(rollback_installer, current_installer)
            _run(["sc.exe", "start", "PartyOpsHost"], timeout=60)
        _set_run(
            run_id,
            status=UpdateStatus.ROLLED_BACK if program_restored else UpdateStatus.FAILED,
            progress=0,
            message=(
                f"Windows 升级未通过，已恢复升级前版本（诊断编号 {run_id[:8]}）"
                if program_restored
                else f"Windows 程序回滚不可用，服务已安全停止（诊断编号 {run_id[:8]}）"
            ),
        )
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def execute_host_update(run_id: str) -> bool:
    settings = get_settings()
    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        if not run or run.target_device_id is not None:
            return False
        package = db.get(UpdatePackage, run.package_id)
        if not package or package.status not in {UpdateStatus.APPLYING, UpdateStatus.VALIDATED}:
            return False
        package_path = settings.updates_dir / package.filename
        manifest = {}
    if not package_path.is_file() or _hash(package_path) != package.sha256:
        _set_run(run_id, status=UpdateStatus.FAILED, progress=0, message="更新包文件缺失或哈希不一致")
        return False
    try:
        with zipfile.ZipFile(package_path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        _set_run(run_id, status=UpdateStatus.FAILED, progress=0, message="更新包清单损坏")
        return False
    if os.name == "nt" and _manifest_has_windows_artifact(manifest):
        return _execute_windows_host_update(run_id, package_path, manifest)
    lock_path = settings.data_dir / ".update.lock"
    if not _acquire_update_lock(lock_path):
        return False
    backup_root = settings.data_dir / "upgrade-backups" / run_id
    rollback_package = backup_root / "partyops-rollback.deb"
    database_backup = backup_root / "partyops.db"
    attachments_backup = backup_root / "attachments"
    archives_backup = backup_root / "archives"
    try:
        _set_run(run_id, status=UpdateStatus.APPLYING, progress=10, message="正在创建升级前快照")
        if not _ensure_dpkg_ready():
            raise RuntimeError("系统包管理器存在未完成配置，已停止本次更新")
        backup_root.mkdir(parents=True, exist_ok=True)
        if settings.database_path.exists():
            _online_backup_database(settings.database_path, database_backup)
        _create_installed_package_snapshot(rollback_package)
        # 升级迁移可能涉及材料索引和附件路径；程序、数据库之外保留受管
        # 附件及归档索引快照，失败时可以完整回到升级前状态。
        if settings.attachments_dir.is_dir():
            shutil.copytree(settings.attachments_dir, attachments_backup, dirs_exist_ok=True)
        if settings.archives_dir.is_dir():
            shutil.copytree(settings.archives_dir, archives_backup, dirs_exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="partyops-update-", dir=settings.transfers_dir) as temporary:
            artifact = _select_artifact(
                package_path,
                manifest,
                _architecture(),
                Path(temporary) / "partyops.deb",
            )
            _set_run(run_id, status=UpdateStatus.APPLYING, progress=35, message="正在停止服务并安装已校验制品")
            _run(["systemctl", "stop", "partyops"], timeout=30)
            result = _run(["dpkg", "--unpack", str(artifact)], timeout=300)
            if result.returncode != 0:
                raise RuntimeError("系统安装器拒绝更新制品")
            result = _run(["dpkg", "--configure", "partyops"], timeout=300)
            if result.returncode != 0:
                raise RuntimeError("系统安装器配置更新失败")
        _run(["systemctl", "start", "partyops"], timeout=60)
        _set_run(run_id, status=UpdateStatus.APPLYING, progress=80, message="正在执行数据库迁移和健康检查")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not _health_check():
            time.sleep(2)
        if not _health_check():
            raise RuntimeError("升级后健康检查未通过")
        with db_runtime.session_factory() as db:
            run = db.get(UpdateRun, run_id)
            package = db.get(UpdatePackage, run.package_id) if run else None
            if run:
                run.status = UpdateStatus.COMPLETED
                run.progress = 100
                run.message = "主机升级完成"
                run.completed_at = utcnow()
            if package:
                package.status = UpdateStatus.COMPLETED
                waiting = _queue_device_updates(package, db)
                if run and waiting:
                    run.message = f"主机升级完成；{waiting} 台协同电脑将在进入系统时确认更新"
            db.commit()
        return True
    except Exception:
        logger.exception("主机升级失败，诊断编号=%s", run_id)
        _run(["systemctl", "stop", "partyops"], timeout=30)
        program_restored = False
        if rollback_package.is_file():
            unpack = _run(["dpkg", "--unpack", str(rollback_package)], timeout=300)
            configure = (
                _run(["dpkg", "--configure", "-a"], timeout=300)
                if unpack.returncode == 0
                else unpack
            )
            program_restored = unpack.returncode == 0 and configure.returncode == 0
        if database_backup.exists():
            Path(f"{settings.database_path}-wal").unlink(missing_ok=True)
            Path(f"{settings.database_path}-shm").unlink(missing_ok=True)
            shutil.copy2(database_backup, settings.database_path)
        _restore_managed_tree(
            attachments_backup,
            settings.attachments_dir,
            settings.data_dir,
        )
        _restore_managed_tree(
            archives_backup,
            settings.archives_dir,
            settings.data_dir,
        )
        if program_restored:
            _run(["systemctl", "start", "partyops"], timeout=60)
        _set_run(
            run_id,
            status=(
                UpdateStatus.ROLLED_BACK
                if program_restored
                else UpdateStatus.FAILED
            ),
            progress=0,
            message=(
                f"升级未通过，已自动恢复升级前版本（诊断编号 {run_id[:8]}）"
                if program_restored
                else f"程序回滚未完成，服务已安全停止（诊断编号 {run_id[:8]}）"
            ),
        )
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def install_device_package(package_path: Path) -> bool:
    """由桌面 Agent 调用；只安装与当前平台和架构匹配的签名制品。"""

    settings = get_settings()
    if not package_path.is_file() or package_path.suffix != ".partyops-update":
        return False
    try:
        architecture = _architecture()
        settings.transfers_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="partyops-device-update-", dir=settings.transfers_dir
        ) as temporary:
            with zipfile.ZipFile(package_path) as archive:
                manifest = json.loads(
                    archive.read("manifest.json").decode("utf-8")
                )
            platform_name = (
                "windows"
                if os.name == "nt" and _manifest_has_windows_artifact(manifest, architecture)
                else "uos"
            )
            # 即使目标版本已经安装，也必须先验证发布签名、架构、大小和哈希。
            # 这样重复更新保持幂等，但伪造或损坏的同版本更新包不会被误报成功。
            artifact = _select_artifact(
                package_path,
                manifest,
                architecture,
                Path(temporary) / ("PartyOps-update.exe" if platform_name == "windows" else "partyops.deb"),
                platform_name,
            )
            if platform_name == "windows":
                from . import __version__

                installed_version = __version__
            else:
                installed_version = _installed_package_version().partition("-")[0]
            if installed_version == str(manifest.get("version", "")):
                return True
            if platform_name == "windows":
                return _run_windows_installer(artifact)
            if not _ensure_dpkg_ready():
                return False
            result = _run(["dpkg", "--unpack", str(artifact)], timeout=300)
            if result.returncode != 0:
                return False
            return (
                _run(["dpkg", "--configure", "partyops"], timeout=300).returncode
                == 0
            )
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile):
        logger.exception("协同电脑更新包校验或安装失败")
        return False


def run_daemon(once: bool = False) -> int:
    while True:
        try:
            with db_runtime.session_factory() as db:
                run = db.scalar(
                    select(UpdateRun)
                    .where(
                        UpdateRun.target_device_id.is_(None),
                        UpdateRun.status == UpdateStatus.APPLYING,
                    )
                    .order_by(UpdateRun.created_at)
                )
                run_id = run.id if run else None
        except OperationalError:
            # 终端模式可能没有主机数据库；更新服务保持空闲，不创建第二份数据库。
            run_id = None
        if run_id:
            execute_host_update(run_id)
        if once:
            return 0
        time.sleep(3)


def _read_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if not separator or not key.startswith("PARTYOPS_"):
            continue
        try:
            tokens = shlex.split(raw)
            values[key] = tokens[0] if tokens else ""
        except ValueError:
            continue
    return values


def _candidate_host_environments() -> list[dict[str, str]]:
    candidates = [Path("/etc/partyops/partyops.env")]
    for base in (Path("/home"), Path("/data/home")):
        if base.is_dir():
            candidates.extend(base.glob("*/.config/partyops/partyops.env"))
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in candidates:
        values = _read_environment(path)
        # 系统服务安装使用默认数据目录时环境文件可以不存在或未显式填写。
        if path == Path("/etc/partyops/partyops.env"):
            values.setdefault("PARTYOPS_MODE", "host")
            values.setdefault("PARTYOPS_DATA_DIR", "/var/lib/partyops")
        if values.get("PARTYOPS_MODE", "host") != "host":
            continue
        data_value = values.get("PARTYOPS_DATA_DIR", "")
        if not data_value:
            continue
        data_dir = Path(data_value)
        if not data_dir.is_absolute():
            continue
        try:
            resolved = data_dir.resolve(strict=False)
        except OSError:
            continue
        allowed = resolved == Path("/var/lib/partyops") or any(
            root == resolved or root in resolved.parents
            for root in (Path("/home"), Path("/data/home"))
        )
        if not allowed or str(resolved) in seen:
            continue
        seen.add(str(resolved))
        values["PARTYOPS_DATA_DIR"] = str(resolved)
        values.setdefault("PARTYOPS_ENVIRONMENT", "production")
        values.setdefault("PARTYOPS_STRICT_SQLITE", "true")
        results.append(values)
    return results


def _pending_run_id(data_dir: Path) -> str | None:
    database = data_dir / "partyops.db"
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT id FROM update_runs "
                "WHERE target_device_id IS NULL AND status='applying' "
                "ORDER BY created_at LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def run_supervisor(once: bool = False) -> int:
    """root 服务发现系统主机和用户模式主机，并为真实数据目录启动受限子进程。"""

    while True:
        for values in _candidate_host_environments():
            run_id = _pending_run_id(Path(values["PARTYOPS_DATA_DIR"]))
            if not run_id:
                continue
            lock_path = Path(values["PARTYOPS_DATA_DIR"]) / ".update.lock"
            if lock_path.exists():
                if not _update_lock_is_stale(lock_path):
                    continue
                # 子进程仍会使用 O_EXCL 原子获取；此处只移除可证实的残锁，
                # 让断电后的 applying 任务无需人工清理即可继续。
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    continue
            environment = os.environ.copy()
            environment.update(values)
            command = (
                [sys.executable, "--run-id", run_id]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "app.update_executor", "--run-id", run_id]
            )
            subprocess.Popen(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        if once:
            return 0
        time.sleep(3)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PartyOps 受限主机更新服务")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--install-package", type=Path)
    parser.add_argument("--supervisor", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.install_package:
        raise SystemExit(0 if install_device_package(args.install_package) else 1)
    if args.supervisor:
        raise SystemExit(run_supervisor(args.once))
    if args.run_id:
        raise SystemExit(0 if execute_host_update(args.run_id) else 1)
    raise SystemExit(run_daemon(args.once))


if __name__ == "__main__":
    main()
