"""主机端受限更新执行器。

该模块只接受数据库中已通过签名校验的更新记录，按照固定架构选择内含
Debian 制品，不执行更新包中的脚本或任意路径；系统级安装由随包的 root
systemd 服务调用，业务进程本身不获得 root 权限。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import logging
import platform
import plistlib
import re
import secrets
import shlex
import sqlite3
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import ssl
from os import fsync as _fsync
from os import getenv as _getenv
from os import replace as _atomic_replace
from pathlib import Path, PurePosixPath

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .backups import verify_backup
from .config import get_settings
from .database import db_runtime
from .models import BackupRun, DeviceCommand, UpdatePackage, UpdateRun, utcnow
from .enums import UpdateStatus
from .platform_info import (
    detect_platform_info,
    normalize_architecture,
    update_platform_key,
)
from .problems import ProblemException
from .upgrades import restore_database_from_upgrade_backup
from .versioning import parse_release_version

logger = logging.getLogger(__name__)

# 更新锁必须能够跨进程阻止重复安装，同时也要能在断电、强制结束进程后
# 自动恢复。旧版本创建的是空锁文件，因此为它保留一个短暂保护期，超过
# 保护期即可安全接管；新锁记录进程和系统启动标识，能准确区分活锁与残锁。
LEGACY_UPDATE_LOCK_GRACE_SECONDS = 300
RPM_PACKAGE_CACHE = Path("/var/cache/partyops/current.rpm")
PERSONAL_NATIVE_ROLLBACK_ROOT = Path("/var/cache/partyops/personal-rollbacks")
UPDATE_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LINUX_PERSONAL_TRANSACTION_COMPLETED = 0
LINUX_PERSONAL_TRANSACTION_ROLLED_BACK = 10
LINUX_PERSONAL_TRANSACTION_FAILED = 20
UPDATE_SNAPSHOT_RESERVE_BYTES = 512 * 1024**2
MAX_HEALTH_RESPONSE_BYTES = 64 * 1024
MAX_UPDATE_MEMBERS = 16
MAX_UPDATE_MANIFEST_BYTES = 1024 * 1024
MAX_UPDATE_ARTIFACT_BYTES = 4 * 1024**3
MAX_UPDATE_EXPANDED_BYTES = 16 * 1024**3
PRIVILEGED_UPDATE_ENV_KEYS = {
    "PARTYOPS_DATA_DIR",
    "PARTYOPS_PORT",
    "PARTYOPS_TLS_ENABLED",
    "PARTYOPS_TLS_CLIENT_CA_FILE",
}


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


def _rollback_digest_path(path: Path) -> Path:
    return Path(f"{path}.sha256")


def _windows_installer_cache() -> Path:
    """SYSTEM 可执行回滚制品不得放进普通用户可能可写的自定义数据目录。"""

    return (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps-System"
        / "installer-cache"
    )


def _update_lock_path(data_dir: Path) -> Path:
    """特权更新锁不得放在普通用户可写的业务数据目录。"""

    if _getenv("PARTYOPS_ENVIRONMENT") == "test":
        return data_dir / ".update.lock"
    if os.name == "nt":
        return (
            Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
            / "PartyOps-System"
            / "update.lock"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "PartyOps" / "update.lock"
    return Path("/var/cache/partyops/update.lock")


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def _secure_update_backup_root(run_id: str) -> Path:
    """为 root/SYSTEM 更新事务创建不受业务进程写入的独占目录。"""

    if not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        raise RuntimeError("更新任务编号无效")
    if _getenv("PARTYOPS_ENVIRONMENT") == "test":
        settings = get_settings()
        data_dir = Path(
            getattr(
                settings,
                "data_dir",
                Path(getattr(settings, "transfers_dir", Path.cwd())).parent,
            )
        )
        base = data_dir / "upgrade-backups"
    elif os.name == "nt":
        base = (
            Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
            / "PartyOps-System"
            / "update-transactions"
        )
    elif sys.platform == "darwin":
        base = (
            Path.home()
            / "Library"
            / "Caches"
            / "PartyOps"
            / "update-transactions"
        )
    else:
        base = Path("/var/cache/partyops/update-transactions")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _is_link_or_reparse_point(base):
        raise RuntimeError("更新事务根目录不能是链接或重解析点")
    if os.name != "nt":
        base.chmod(0o700)
    transaction = base / run_id
    if transaction.exists() or _is_link_or_reparse_point(transaction):
        raise RuntimeError("更新事务目录已存在，拒绝复用不完整快照")
    transaction.mkdir(mode=0o700)
    return transaction


def _assert_managed_tree_has_no_links(path: Path) -> None:
    if _is_link_or_reparse_point(path):
        raise RuntimeError(f"受管目录不能是链接或重解析点：{path}")
    if not path.exists():
        return
    for current, directories, filenames in os.walk(path, followlinks=False):
        for name in [*directories, *filenames]:
            candidate = Path(current) / name
            if _is_link_or_reparse_point(candidate):
                raise RuntimeError(f"受管目录包含链接或重解析点：{candidate}")


def _snapshot_managed_tree(source: Path, destination: Path, data_root: Path) -> None:
    resolved_root = data_root.resolve(strict=False)
    if _is_link_or_reparse_point(source):
        raise RuntimeError("受管数据源不能是链接或重解析点")
    resolved_source = source.resolve(strict=False)
    if (
        resolved_source != resolved_root
        and resolved_root not in resolved_source.parents
    ):
        raise RuntimeError("受管数据源超出 PartyOps 数据目录")
    if not source.is_dir():
        return
    _assert_managed_tree_has_no_links(source)
    shutil.copytree(source, destination, symlinks=True)
    # 若本地进程在扫描与复制之间插入链接，复制完成后的第二次检查会失败，
    # 事务仍处于程序未修改阶段，旧服务随后恢复。
    _assert_managed_tree_has_no_links(destination)


def _managed_tree_size(path: Path) -> int:
    if not path.exists():
        return 0
    _assert_managed_tree_has_no_links(path)
    total = 0
    for current, _directories, filenames in os.walk(path, followlinks=False):
        for name in filenames:
            total += (Path(current) / name).stat().st_size
    return total


def _ensure_update_snapshot_space(backup_root: Path, settings) -> None:
    required = 0
    if settings.database_path.is_file():
        required += settings.database_path.stat().st_size
    required += _managed_tree_size(settings.attachments_dir)
    required += _managed_tree_size(settings.archives_dir)
    # 文件系统元数据、SQLite 页和复制过程中轻微增长保留 10% 浮动空间。
    required = int(required * 1.1)
    free = shutil.disk_usage(backup_root).free
    if required + UPDATE_SNAPSHOT_RESERVE_BYTES > free:
        raise RuntimeError(
            "UPDATE_SNAPSHOT_DISK_FULL：升级一致快照空间不足，未停止服务或修改程序"
        )


def _remove_secure_update_transaction(path: Path | None) -> bool:
    if path is None or not path.exists():
        return True
    if _is_link_or_reparse_point(path) or not UPDATE_RUN_ID_PATTERN.fullmatch(
        path.name
    ):
        logger.error("拒绝清理异常更新事务目录：%s", path)
        return False
    try:
        shutil.rmtree(path)
        return True
    except OSError:
        logger.exception("更新事务目录未能清理：%s", path)
        return False


def _verify_cached_rollback_artifact(path: Path) -> bool:
    """只信任带独立 SHA-256 记录且内容一致的本地回滚制品。"""

    try:
        expected = (
            _rollback_digest_path(path).read_text(encoding="ascii").strip().lower()
        )
    except OSError:
        return False
    if len(expected) != 64 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        return False
    try:
        actual = _hash(path)
    except OSError:
        return False
    return hmac.compare_digest(actual, expected)


def _cache_verified_rollback_artifact(source: Path, target: Path) -> None:
    """流式复制并分别原子切换制品及哈希；中断后宁可拒绝升级。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_artifact: Path | None = None
    temporary_digest: Path | None = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".incoming",
            delete=False,
        ) as destination:
            temporary_artifact = Path(destination.name)
            with source.open("rb") as input_handle:
                while chunk := input_handle.read(1024 * 1024):
                    destination.write(chunk)
                    digest.update(chunk)
            destination.flush()
            _fsync(destination.fileno())
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.sha256.",
            suffix=".incoming",
            delete=False,
        ) as digest_handle:
            temporary_digest = Path(digest_handle.name)
            digest_handle.write(digest.hexdigest())
            digest_handle.flush()
            _fsync(digest_handle.fileno())
        _atomic_replace(temporary_artifact, target)
        temporary_artifact = None
        _atomic_replace(temporary_digest, _rollback_digest_path(target))
        temporary_digest = None
    finally:
        if temporary_artifact is not None:
            temporary_artifact.unlink(missing_ok=True)
        if temporary_digest is not None:
            temporary_digest.unlink(missing_ok=True)


def _personal_native_rollback_paths(run_id: str) -> tuple[Path, Path]:
    if not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        raise RuntimeError("个人模式回滚编号无效")
    return (
        PERSONAL_NATIVE_ROLLBACK_ROOT / f"{run_id}.package",
        PERSONAL_NATIVE_ROLLBACK_ROOT / f"{run_id}.json",
    )


def _persist_personal_native_rollback(
    run_id: str,
    artifact: Path,
    *,
    platform_name: str,
    previous_version: str,
    target_version: str,
) -> None:
    """在改动系统包前保存 root 独占的上一版本，供健康失败后二阶段回滚。"""

    if (
        platform_name not in {"linux-deb", "uos", "linux-rpm"}
        or not previous_version
        or not target_version
        or previous_version == target_version
    ):
        raise RuntimeError("个人模式原生包回滚元数据无效")
    rollback_path, metadata_path = _personal_native_rollback_paths(run_id)
    PERSONAL_NATIVE_ROLLBACK_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _is_link_or_reparse_point(PERSONAL_NATIVE_ROLLBACK_ROOT):
        raise RuntimeError("个人模式回滚目录不能是链接")
    PERSONAL_NATIVE_ROLLBACK_ROOT.chmod(0o700)
    _cache_verified_rollback_artifact(artifact, rollback_path)
    payload = json.dumps(
        {
            "format_version": 1,
            "platform": platform_name,
            "previous_version": previous_version,
            "target_version": target_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    incoming = metadata_path.with_suffix(".json.incoming")
    incoming.write_text(payload, encoding="utf-8")
    incoming.chmod(0o600)
    with incoming.open("rb+") as handle:
        _fsync(handle.fileno())
    _atomic_replace(incoming, metadata_path)


def _discard_personal_native_rollback(run_id: str) -> None:
    rollback_path, metadata_path = _personal_native_rollback_paths(run_id)
    for path in (rollback_path, _rollback_digest_path(rollback_path), metadata_path):
        if _is_link_or_reparse_point(path):
            raise RuntimeError("个人模式回滚文件不能是链接")
        path.unlink(missing_ok=True)


def _rollback_linux_personal_package_locked(run_id: str) -> bool:
    """在调用方持有全局包锁时恢复上一版本，并拒绝跨事务降级。"""

    if os.name == "nt" or not sys.platform.startswith("linux"):
        return False
    rollback_path, metadata_path = _personal_native_rollback_paths(run_id)
    try:
        if _is_link_or_reparse_point(metadata_path):
            return False
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        platform_name = str(metadata.get("platform", ""))
        previous_version = str(metadata.get("previous_version", ""))
        target_version = str(metadata.get("target_version", ""))
        if (
            metadata.get("format_version") != 1
            or platform_name not in {"linux-deb", "uos", "linux-rpm"}
            or not previous_version
            or not target_version
            or previous_version == target_version
            or not _verify_cached_rollback_artifact(rollback_path)
        ):
            return False
        current_version = _linux_native_version(platform_name)
        if current_version != target_version:
            logger.error(
                "拒绝跨事务回滚：当前版本=%s，事务目标=%s，诊断编号=%s",
                current_version,
                target_version,
                run_id,
            )
            return False
        if platform_name == "linux-rpm":
            restored = _install_rpm(rollback_path, allow_downgrade=True)
            restored = (
                restored and _linux_native_version(platform_name) == previous_version
            )
            if restored:
                _cache_verified_rollback_artifact(rollback_path, RPM_PACKAGE_CACHE)
        else:
            unpack = _run_linux_package_manager(
                ["dpkg", "--unpack", str(rollback_path)], timeout=300
            )
            configure = (
                _run_linux_package_manager(["dpkg", "--configure", "-a"], timeout=300)
                if unpack.returncode == 0
                else unpack
            )
            restored = unpack.returncode == 0 and configure.returncode == 0
            restored = (
                restored and _linux_native_version(platform_name) == previous_version
            )
        if restored:
            _discard_personal_native_rollback(run_id)
        return restored
    except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        logger.exception("个人模式原生包回滚失败，诊断编号=%s", run_id)
        return False


def _system_boot_id() -> str:
    try:
        return (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
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
    if _is_link_or_reparse_point(lock_path.parent) or _is_link_or_reparse_point(
        lock_path
    ):
        return False
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
        write_failed = False
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
            write_failed = True
        finally:
            os.close(lock_fd)
        if write_failed:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
    return False


def _trusted_public_key() -> str:
    settings = get_settings()
    # 生产特权更新器不能信任业务配置或继承环境里的根公钥。个人模式数据目录
    # 可由登录用户写入，若优先读取其中的值，UAC 后的进程会被诱导信任伪造包。
    # 测试/开发仍允许显式注入临时密钥，生产只读安装目录或 /etc 的受保护副本。
    runtime_environment = str(
        getattr(
            settings,
            "environment",
            os.getenv("PARTYOPS_ENVIRONMENT", "production"),
        )
    )
    if runtime_environment in {"test", "development"} and settings.update_public_key:
        return settings.update_public_key.strip()
    candidates = [Path("/etc/partyops/update-public-key")]
    if sys.platform == "darwin":
        # PKG 把根公钥放在 root 所有的 PartyOps.app/Contents/Resources。
        # 公钥不是嵌套代码，不能放进 MacOS；也不从用户 Application
        # Support 或环境变量回退。
        executable_parent = Path(sys.executable).resolve().parent
        candidates = [
            executable_parent.parent / "Resources" / "update-public-key.txt"
        ]
    elif os.name == "nt":
        # SYSTEM 更新器只信任与冻结程序同目录、由安装器写入 Program Files
        # 的公钥；不再从业务数据目录回退，避免自定义目录 ACL 被误配后替换根信任。
        candidates = [Path(sys.executable).resolve().parent / "update-public-key.txt"]
    for path in candidates:
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _is_link_or_reparse_point(path)
            or metadata.st_size > 4096
        ):
            continue
        if os.name != "nt" and (
            int(getattr(metadata, "st_uid", -1)) != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            continue
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"更新清单包含重复字段：{key}")
        result[key] = value
    return result


def _read_update_manifest(package_path: Path) -> dict:
    """特权执行器独立重验 ZIP 结构，不能只依赖业务进程的上传校验。"""

    with zipfile.ZipFile(package_path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_UPDATE_MEMBERS:
            raise RuntimeError("更新包文件数量异常")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise RuntimeError("更新包包含重复文件")
        info_by_name = {info.filename: info for info in infos}
        for info in infos:
            _safe_member(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir() or mode not in {0, stat.S_IFREG}:
                raise RuntimeError("更新包包含非普通文件")
        manifest_info = info_by_name.get("manifest.json")
        if (
            manifest_info is None
            or manifest_info.file_size <= 0
            or manifest_info.file_size > MAX_UPDATE_MANIFEST_BYTES
        ):
            raise RuntimeError("更新包清单缺失或体积异常")
        raw = archive.read(manifest_info)
    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    if not isinstance(manifest, dict):
        raise RuntimeError("更新包清单结构无效")
    version = manifest.get("version")
    artifacts = manifest.get("artifacts")
    if (
        not isinstance(version, str)
        or not version.strip()
        or not isinstance(artifacts, dict)
    ):
        raise RuntimeError("更新包清单字段不完整")
    expanded_size = 0
    allowed = {"manifest.json", "RELEASE-NOTES.txt", *artifacts.keys()}
    if set(info_by_name) - allowed:
        raise RuntimeError("更新包包含未登记文件")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or not isinstance(expected, dict):
            raise RuntimeError("更新包制品清单结构无效")
        _safe_member(name)
        info = info_by_name.get(name)
        try:
            expected_size = int(expected.get("size", -1))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("更新包制品大小无效") from exc
        expected_hash = str(expected.get("sha256", "")).lower()
        if (
            info is None
            or expected_size < 0
            or expected_size > MAX_UPDATE_ARTIFACT_BYTES
            or info.file_size != expected_size
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
        ):
            raise RuntimeError("更新包制品元数据无效")
        expanded_size += expected_size
        if expanded_size > MAX_UPDATE_EXPANDED_BYTES:
            raise RuntimeError("更新包展开体积异常")
    return manifest


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
    value = normalize_architecture(platform.machine())
    supported = {"amd64", "x86"} if os.name == "nt" else {"amd64", "arm64"}
    if value not in supported:
        raise RuntimeError("当前系统架构不在 PartyOps 支持范围")
    return value


def _manifest_platform_name(manifest: dict) -> str:
    """选择 v3 精确平台键，并为 v2 保留 windows/uos 兼容。"""

    raw_format_version = manifest.get("format_version", 2)
    if type(raw_format_version) is not int or raw_format_version not in {2, 3, 4}:
        raise RuntimeError("更新包格式版本无效")
    if raw_format_version >= 3:
        value = update_platform_key(detect_platform_info())
        if not value:
            raise RuntimeError("当前系统无法匹配 PartyOps 更新制品")
        return value
    return "windows" if os.name == "nt" else "uos"


def _run(
    command: list[str],
    *,
    timeout: int = 120,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_environment = None
    if environment:
        process_environment = dict(os.environ)
        process_environment.update(environment)
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=process_environment,
    )


def _run_linux_package_manager(
    command: list[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    """执行系统内包升级，同时避免包脚本停止承载当前事务的更新服务。"""

    return _run(
        command,
        timeout=timeout,
        environment={"PARTYOPS_IN_APP_UPDATE": "1"},
    )


def _ensure_dpkg_ready() -> bool:
    """只收敛 PartyOps 自己的半配置状态，不擅自修改其他系统软件包。"""

    audit = _run(["dpkg", "--audit"], timeout=60)
    if audit.returncode != 0:
        logger.error("dpkg_audit_failed returncode=%s", audit.returncode)
        return False
    all_pending = "\n".join(line.rstrip() for line in audit.stdout.splitlines()).strip()
    if not all_pending:
        return True

    partyops_audit = _run(["dpkg", "--audit", "partyops"], timeout=60)
    partyops_pending = "\n".join(
        line.rstrip() for line in partyops_audit.stdout.splitlines()
    ).strip()
    if (
        partyops_audit.returncode != 0
        or not partyops_pending
        or partyops_pending != all_pending
    ):
        logger.error(
            "dpkg_unrelated_pending_packages detected; refuse automatic system-wide configure"
        )
        return False

    result = _run_linux_package_manager(
        ["dpkg", "--configure", "partyops"], timeout=300
    )
    if result.returncode != 0:
        logger.error("dpkg_partyops_repair_failed returncode=%s", result.returncode)
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
        if status in {
            UpdateStatus.COMPLETED,
            UpdateStatus.FAILED,
            UpdateStatus.ROLLED_BACK,
        }:
            run.completed_at = utcnow()
        db.commit()


def _assert_update_not_downgrade(manifest: dict) -> None:
    """签名旧包也不能被 root/SYSTEM 重放为降级安装。"""

    settings = get_settings()
    target_raw = str(manifest.get("version") or "").strip()
    current_raw = str(getattr(settings, "app_version", "") or "").strip()
    try:
        target = parse_release_version(target_raw)
        current = parse_release_version(current_raw)
    except ProblemException as exc:
        raise RuntimeError("更新包或当前程序版本号无效") from exc
    if target < current:
        raise RuntimeError(
            f"UPDATE_DOWNGRADE_DENIED：拒绝从 {current_raw} 降级到 {target_raw}"
        )
    minimum_raw = str(manifest.get("min_version") or "").strip()
    if minimum_raw:
        try:
            minimum = parse_release_version(minimum_raw)
        except ProblemException as exc:
            raise RuntimeError("更新包最低兼容版本号无效") from exc
        if minimum > target:
            raise RuntimeError("更新包最低兼容版本高于目标版本")
        if current < minimum:
            raise RuntimeError(
                f"UPDATE_BRIDGE_REQUIRED：当前 {current_raw}，需先升级到 {minimum_raw}"
            )


def _select_artifact(
    package_path: Path,
    manifest: dict,
    architecture: str,
    target: Path,
    platform_name: str = "uos",
) -> Path:
    if not _verify_manifest_signature(manifest):
        raise RuntimeError("更新包发布签名无效")
    _assert_update_not_downgrade(manifest)
    artifacts = manifest.get("artifacts", {})
    platform_artifacts = manifest.get("platform_artifacts", {})
    platform_map = (
        platform_artifacts.get(platform_name, {})
        if isinstance(platform_artifacts, dict)
        else {}
    )
    architecture_artifacts = manifest.get("architecture_artifacts", {})
    expected_name = (
        str(platform_map.get(architecture, ""))
        if isinstance(platform_map, dict)
        else ""
    )
    if not expected_name and platform_name == "uos":
        expected_name = (
            str(architecture_artifacts.get(architecture, ""))
            if isinstance(architecture_artifacts, dict)
            else ""
        )
    if not expected_name:
        raise RuntimeError(f"更新包不包含 {architecture} 安装制品")
    expected_suffixes = {
        "windows": {"amd64": "_windows_amd64.exe"},
        "windows7": {
            "amd64": "_windows7_amd64.exe",
            "x86": "_windows7_x86.exe",
        },
        "linux-deb": {
            "amd64": "_linux_amd64.deb",
            "arm64": "_linux_arm64.deb",
        },
        "linux-rpm": {
            "amd64": ".x86_64.rpm",
            "arm64": ".aarch64.rpm",
        },
        "macos": {
            "amd64": "_macos_x86_64.pkg",
            "arm64": "_macos_arm64.pkg",
        },
        "uos": {
            "amd64": "_amd64.deb",
            "arm64": "_arm64.deb",
        },
    }
    expected_suffix = expected_suffixes.get(platform_name, {}).get(architecture, "")
    if not expected_suffix:
        raise RuntimeError("当前平台与架构没有允许的更新制品类型")
    if expected_name not in artifacts or not expected_name.endswith(expected_suffix):
        raise RuntimeError("架构安装制品与清单不一致")
    with zipfile.ZipFile(package_path) as archive:
        _safe_member(expected_name)
        matching = [
            info for info in archive.infolist() if info.filename == expected_name
        ]
        if len(matching) != 1:
            raise RuntimeError("更新包制品缺失或包含重复文件")
        info = matching[0]
        mode = (info.external_attr >> 16) & 0o170000
        if info.is_dir() or mode not in {0, stat.S_IFREG}:
            raise RuntimeError("更新包制品不是普通文件")
        expected = artifacts.get(expected_name, {})
        if int(expected.get("size", -1)) != info.file_size:
            raise RuntimeError("安装制品大小与清单不一致")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        digest = hashlib.sha256()
        written = 0
        try:
            # 先写入同目录、系统随机命名的独占临时文件，完整验签/验哈希后再
            # 原子替换最终缓存。这样失败不会破坏旧回滚制品，也不会跟随攻击者
            # 预先放置的同名链接或文件。
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".verified",
                delete=False,
            ) as destination:
                temporary_path = Path(destination.name)
                with archive.open(info) as source:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                destination.flush()
                _fsync(destination.fileno())
            if written != info.file_size:
                raise RuntimeError("安装制品实际长度与清单不一致")
            if str(expected.get("sha256", "")).lower() != digest.hexdigest():
                raise RuntimeError("安装制品哈希与清单不一致")
            _atomic_replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
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


def _installed_rpm_version() -> str:
    result = _run(
        ["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "partyops"], timeout=20
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _partyops_version_from_native(raw: str, package_format: str) -> str:
    """把 DEB/RPM 原生版本规范映射为应用版本，供更新幂等判断。"""

    value = raw.strip()
    if package_format == "deb":
        if ":" in value:
            value = value.split(":", 1)[1]
        value = value.split("+", 1)[0].replace("~rc.", "-rc.")
        return value
    if package_format == "rpm":
        match = re.fullmatch(r"(\d+\.\d+\.\d+)-0\.rc\.(\d+)(?:\.\d+)?", value)
        if match:
            return f"{match.group(1)}-rc.{match.group(2)}"
        return value
    return value


def _install_rpm(path: Path, *, allow_downgrade: bool = False) -> bool:
    package_manager = shutil.which("dnf") or shutil.which("yum")
    if not package_manager:
        return False
    action = "downgrade" if allow_downgrade else "install"
    result = _run_linux_package_manager(
        [package_manager, "-y", action, str(path)], timeout=600
    )
    return result.returncode == 0


def _execute_linux_rpm_host_update(
    run_id: str,
    package_path: Path,
    manifest: dict,
) -> bool:
    """RPM 主机适配器：只在存在可安装的上一版本缓存时执行可回滚升级。"""

    settings = get_settings()
    lock_path = _update_lock_path(settings.data_dir)
    if not _acquire_update_lock(lock_path):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="已有更新事务占用本机；本次重复任务已安全终止，请等待后重试",
        )
        return False
    backup_root: Path | None = None
    # 以下路径在完成安全事务目录创建后再绑定，避免 root/SYSTEM 在业务用户
    # 可写的数据根目录中跟随预置链接。
    database_backup: Path | None = None
    attachments_backup: Path | None = None
    archives_backup: Path | None = None
    rollback_package: Path | None = None
    package_cache = RPM_PACKAGE_CACHE
    mutation_started = False
    service_stopped = False
    try:
        if not _verify_cached_rollback_artifact(package_cache):
            raise RuntimeError(
                "RPM_UPGRADE_ROLLBACK_INVALID：当前版本 RPM 缓存缺失或校验失败，拒绝不可回滚升级"
            )
        backup_root = _secure_update_backup_root(run_id)
        database_backup = backup_root / "partyops.db"
        attachments_backup = backup_root / "attachments"
        archives_backup = backup_root / "archives"
        rollback_package = backup_root / "partyops-rollback.rpm"
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=10,
            message="正在验证 RPM 制品与回滚能力",
        )
        _cache_verified_rollback_artifact(package_cache, rollback_package)
        with tempfile.TemporaryDirectory(
            prefix="partyops-update-", dir=backup_root
        ) as temporary:
            artifact = _select_artifact(
                package_path,
                manifest,
                _architecture(),
                Path(temporary) / "partyops.rpm",
                "linux-rpm",
            )
            _ensure_update_snapshot_space(backup_root, settings)
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=25,
                message="正在停止服务并创建一致快照",
            )
            stop_result = _run(["systemctl", "stop", "partyops"], timeout=30)
            if stop_result.returncode != 0:
                raise RuntimeError(
                    "RPM_UPGRADE_SERVICE_STOP_FAILED：主机服务未能安全停止"
                )
            service_stopped = True
            if settings.database_path.exists():
                _online_backup_database(settings.database_path, database_backup)
            if settings.attachments_dir.is_dir():
                _snapshot_managed_tree(
                    settings.attachments_dir, attachments_backup, settings.data_dir
                )
            if settings.archives_dir.is_dir():
                _snapshot_managed_tree(
                    settings.archives_dir, archives_backup, settings.data_dir
                )
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=35,
                message="一致快照完成，正在安装 RPM",
            )
            mutation_started = True
            if not _install_rpm(artifact):
                raise RuntimeError("RPM 包管理器拒绝更新制品")
            package_cache.parent.mkdir(parents=True, exist_ok=True)
            _cache_verified_rollback_artifact(artifact, package_cache)
        if _run(["systemctl", "start", "partyops"], timeout=60).returncode != 0:
            raise RuntimeError(
                "RPM_UPGRADE_SERVICE_START_FAILED：升级后主机服务未能启动"
            )
        service_stopped = False
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=80,
            message="正在执行数据库迁移和健康检查",
        )
        target_version = str(manifest.get("version") or "").strip() or None
        if not _wait_for_health(target_version, 90):
            raise RuntimeError("RPM 升级后健康检查未通过")
        with db_runtime.session_factory() as db:
            run = db.get(UpdateRun, run_id)
            package = db.get(UpdatePackage, run.package_id) if run else None
            if run:
                run.status = UpdateStatus.COMPLETED
                run.progress = 100
                run.message = "RPM 主机升级完成"
                run.completed_at = utcnow()
            if package:
                package.status = UpdateStatus.COMPLETED
                _queue_device_updates(package, db)
            db.commit()
        _remove_secure_update_transaction(backup_root)
        return True
    except Exception:
        logger.exception("RPM 主机升级失败，诊断编号=%s", run_id)
        if not mutation_started:
            restarted = (
                not service_stopped
                or _run(["systemctl", "start", "partyops"], timeout=60).returncode == 0
            )
            _remove_secure_update_transaction(backup_root)
            _set_run(
                run_id,
                status=UpdateStatus.FAILED,
                progress=0,
                message=(
                    f"RPM 升级在修改程序前停止，原版本保持不变并继续运行（诊断编号 {run_id[:8]}）"
                    if restarted
                    else f"RPM 升级未修改程序，但原服务恢复失败（诊断编号 {run_id[:8]}）"
                ),
            )
            return False
        _run(["systemctl", "stop", "partyops"], timeout=30)
        service_stopped = True
        program_restored = False
        try:
            program_restored = bool(
                rollback_package is not None
                and _verify_cached_rollback_artifact(rollback_package)
                and _install_rpm(rollback_package, allow_downgrade=True)
            )
            if database_backup is not None and database_backup.exists():
                _restore_database_snapshot(database_backup, settings.database_path)
            if attachments_backup is not None:
                _restore_managed_tree(
                    attachments_backup, settings.attachments_dir, settings.data_dir
                )
            if archives_backup is not None:
                _restore_managed_tree(
                    archives_backup, settings.archives_dir, settings.data_dir
                )
            if program_restored and rollback_package is not None:
                _cache_verified_rollback_artifact(rollback_package, package_cache)
                service_stopped = (
                    _run(["systemctl", "start", "partyops"], timeout=60).returncode != 0
                )
                previous_version = (
                    str(getattr(settings, "app_version", "")).strip() or None
                )
                program_restored = not service_stopped and _wait_for_health(
                    previous_version, 60
                )
                if not program_restored:
                    _run(["systemctl", "stop", "partyops"], timeout=30)
                    service_stopped = True
        except Exception:
            logger.exception("RPM 回滚未完成，诊断编号=%s", run_id)
            program_restored = False
        _set_run(
            run_id,
            status=UpdateStatus.ROLLED_BACK
            if program_restored
            else UpdateStatus.FAILED,
            progress=0,
            message=(
                f"RPM 升级未通过，已恢复上一版本（诊断编号 {run_id[:8]}）"
                if program_restored
                else f"RPM 回滚不可用，服务已安全停止（诊断编号 {run_id[:8]}）"
            ),
        )
        if program_restored:
            _remove_secure_update_transaction(backup_root)
        return False
    finally:
        lock_path.unlink(missing_ok=True)


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
    _assert_managed_tree_has_no_links(backup)
    if _is_link_or_reparse_point(destination):
        raise RuntimeError("回滚目标不能是链接或重解析点")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(backup, destination, symlinks=True)


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


def _restore_database_snapshot(snapshot: Path, destination: Path) -> None:
    """关闭连接池后原子换回 SQLite 快照，失败时恢复换库前文件。"""

    check = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA quick_check").fetchone()
    finally:
        check.close()
    if not result or result[0] != "ok":
        raise RuntimeError("数据库回滚快照完整性检查未通过")
    incoming = destination.with_name(f".{destination.name}.rollback-incoming")
    previous = destination.with_name(f".{destination.name}.before-rollback")
    incoming.unlink(missing_ok=True)
    previous.unlink(missing_ok=True)
    with db_runtime.exclusive_maintenance(timeout_seconds=30):
        db_runtime.dispose()
        moved_previous = False
        try:
            Path(f"{destination}-wal").unlink(missing_ok=True)
            Path(f"{destination}-shm").unlink(missing_ok=True)
            shutil.copy2(snapshot, incoming)
            # Windows 对只读句柄的 FlushFileBuffers 行为不一致；以可写句柄
            # 打开临时副本，确保数据真正落盘后才执行原子切换。
            with incoming.open("rb+") as handle:
                _fsync(handle.fileno())
            if destination.exists():
                _atomic_replace(destination, previous)
                moved_previous = True
            _atomic_replace(incoming, destination)
            verify = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
            try:
                verified = verify.execute("PRAGMA quick_check").fetchone()
            finally:
                verify.close()
            if not verified or verified[0] != "ok":
                raise RuntimeError("恢复后的数据库完整性检查未通过")
            previous.unlink(missing_ok=True)
        except Exception:
            incoming.unlink(missing_ok=True)
            if moved_previous and previous.exists():
                destination.unlink(missing_ok=True)
                _atomic_replace(previous, destination)
            raise
        finally:
            db_runtime.rebuild()


def _health_check(expected_version: str | None = None) -> bool:
    """确认升级后的 PartyOps 主机已运行目标版本且数据库能力完整。

    仅有 HTTP 200 不能证明更新成功。旧进程、错误模式进程或被本机其他
    服务占用的端口都可能返回 200，因此这里同时校验内部 CA、响应结构、
    主机模式、目标版本以及 SQLite/FTS5 能力。
    """

    settings = get_settings()
    scheme = "https" if settings.tls_enabled else "http"
    request = urllib.request.Request(
        f"{scheme}://127.0.0.1:{settings.port}/api/v1/health"
    )
    try:
        context = None
        if settings.tls_enabled:
            material = settings.tls_client_ca_file or (
                settings.data_dir / "secrets" / "pki" / "ca.pem"
            )
            if not material.is_file():
                return False
            context = ssl.create_default_context(cafile=str(material.resolve()))
        with urllib.request.urlopen(  # nosec B310 - scheme 固定为 HTTP(S)，主机来自已校验运行配置。
            request,
            timeout=5,
            context=context,
        ) as response:
            if response.status != 200:
                return False
            raw = response.read(MAX_HEALTH_RESPONSE_BYTES + 1)
            if len(raw) > MAX_HEALTH_RESPONSE_BYTES:
                return False
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return False
            configured_mode = str(getattr(settings, "mode", "host"))
            expected_mode = (
                configured_mode if configured_mode in {"host", "personal"} else "host"
            )
            if payload.get("status") != "ok" or payload.get("mode") != expected_mode:
                return False
            sqlite_info = payload.get("sqlite")
            if not isinstance(sqlite_info, dict):
                return False
            if (
                sqlite_info.get("safe_version") is not True
                or sqlite_info.get("fts5") is not True
            ):
                return False
            reported_version = str(payload.get("app_version") or "").strip()
            if not reported_version:
                return False
            parse_release_version(reported_version)
            if expected_version and (
                parse_release_version(reported_version)
                != parse_release_version(expected_version)
            ):
                return False
            return True
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        OSError,
        ProblemException,
        urllib.error.URLError,
        TimeoutError,
    ):
        return False


def _wait_for_health(expected_version: str | None, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _health_check(expected_version):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(2.0, remaining))


def _manifest_has_windows_artifact(manifest: dict, architecture: str = "amd64") -> bool:
    platforms = manifest.get("platform_artifacts", {})
    platform_name = "windows"
    raw_format_version = manifest.get("format_version", 2)
    if type(raw_format_version) is not int or raw_format_version not in {2, 3, 4}:
        return False
    if raw_format_version >= 3:
        detected = update_platform_key(detect_platform_info())
        if detected in {"windows", "windows7"}:
            platform_name = detected
        else:
            return False
    return bool(
        isinstance(platforms, dict)
        and isinstance(platforms.get(platform_name), dict)
        and platforms[platform_name].get(architecture)
    )


def _run_windows_installer(path: Path, *, service_handoff: bool = False) -> bool:
    command = [
        str(path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
    ]
    if service_handoff:
        # 主机更新器正由 PartyOpsUpdateService 承载，安装器不能停止自己的
        # 父服务。两个更新进程采用 Windows 重启替换，其余程序立即升级，
        # 当前事务才能继续执行健康检查和失败回滚。
        command.append("/INAPPUPDATE=1")
    result = _run(command, timeout=900)
    return result.returncode == 0


def _macos_application_path() -> Path:
    """正式环境只更新固定 Applications 目录；测试可注入隔离路径。"""

    if _getenv("PARTYOPS_ENVIRONMENT") == "test":
        override = _getenv("PARTYOPS_MACOS_APP_PATH", "").strip()
        if override:
            return Path(override).expanduser().resolve()
    return Path("/Applications/PartyOps.app")


def _macos_bundle_version(app_path: Path) -> str:
    if app_path.is_symlink():
        return ""
    info = app_path / "Contents" / "Info.plist"
    if not info.is_file() or info.is_symlink():
        return ""
    try:
        payload = plistlib.loads(info.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("CFBundleShortVersionString") or "").strip()


def _macos_application_is_trusted(app_path: Path) -> bool:
    """同时核验完整签名和 Gatekeeper。

    版本号只是元数据，不能证明快照或新安装的 app 仍是官方制品。
    """

    if not app_path.is_dir() or app_path.is_symlink():
        return False
    signature = _run(
        ["/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(app_path)],
        timeout=120,
    )
    if signature.returncode != 0:
        return False
    assessment = _run(
        ["/usr/sbin/spctl", "--assess", "--type", "execute", str(app_path)],
        timeout=120,
    )
    return assessment.returncode == 0


def _macos_process_path(pid: int) -> Path | None:
    if sys.platform != "darwin" or pid <= 0:
        return None
    try:
        import ctypes

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidpath = libproc.proc_pidpath
        proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        length = proc_pidpath(pid, buffer, len(buffer))
        if length <= 0:
            return None
        return Path(os.fsdecode(buffer.raw[:length])).resolve()
    except (OSError, ValueError):
        return None


def _stop_macos_runtime(app_path: Path, port: int) -> bool:
    """只终止固定 app 内、真实监听 PartyOps 端口的进程。"""

    if sys.platform != "darwin" or not 1024 <= port <= 65534:
        return False
    for label in ("cn.partyops.host", "cn.partyops.personal"):
        _run(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
            timeout=30,
        )
    result = _run(
        [
            "/usr/sbin/lsof",
            "-nP",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ],
        timeout=15,
    )
    pids = {
        int(value)
        for value in result.stdout.split()
        if value.isdigit() and int(value) != os.getpid()
    }
    expected = (app_path / "Contents" / "MacOS" / "partyops").resolve()
    for pid in pids:
        if _macos_process_path(pid) != expected:
            raise RuntimeError("macOS PartyOps 端口被身份不明的进程占用，拒绝更新")
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if all(_macos_process_path(pid) is None for pid in pids):
            return True
        time.sleep(0.25)
    raise RuntimeError("macOS 旧版 PartyOps 进程未能安全退出")


def _run_macos_privileged_installer(package_path: Path) -> bool:
    """通过系统管理员授权安装已验签 PKG，不拼接调用者可控 shell 文本。"""

    script = (
        "on run argv\n"
        "set packagePath to item 1 of argv\n"
        "do shell script \"/usr/sbin/installer -pkg \" & quoted form of packagePath "
        "& \" -target /\" with administrator privileges\n"
        "end run"
    )
    result = _run(
        ["/usr/bin/osascript", "-e", script, str(package_path.resolve())],
        timeout=900,
    )
    return result.returncode == 0


def _restore_macos_application(snapshot: Path, app_path: Path, failed: Path) -> bool:
    """把失败的新 app 移入事务目录，再以旧签名快照恢复；不递归删除。"""

    if failed.exists() or not _macos_application_is_trusted(snapshot):
        return False
    script = (
        "on run argv\n"
        "set sourcePath to item 1 of argv\n"
        "set appPath to item 2 of argv\n"
        "set failedPath to item 3 of argv\n"
        "do shell script \"if [ -e \" & quoted form of appPath & \" ]; then /bin/mv \" "
        "& quoted form of appPath & \" \" & quoted form of failedPath "
        "& \"; fi && /usr/bin/ditto --rsrc --extattr --acl \" "
        "& quoted form of sourcePath & \" \" & quoted form of appPath "
        "with administrator privileges\n"
        "end run"
    )
    result = _run(
        [
            "/usr/bin/osascript",
            "-e",
            script,
            str(snapshot.resolve()),
            str(app_path.resolve()),
            str(failed.resolve()),
        ],
        timeout=900,
    )
    if result.returncode == 0 and _macos_application_is_trusted(app_path):
        return True
    # 快照在验证与特权复制之间仍可能被同账号进程替换。
    # 如恢复后签名不再可信，必须把它移出 Applications 并放回
    # 刚才的官方新 app，不能在失败路径留下未验签应用。
    invalid = failed.with_name("PartyOps-invalid-restored.app")
    recovery_script = (
        "on run argv\n"
        "set appPath to item 1 of argv\n"
        "set failedPath to item 2 of argv\n"
        "set invalidPath to item 3 of argv\n"
        "do shell script \"if [ -e \" & quoted form of appPath "
        "& \" ]; then /bin/mv \" & quoted form of appPath & \" \" "
        "& quoted form of invalidPath & \"; fi; /bin/mv \" "
        "& quoted form of failedPath & \" \" & quoted form of appPath "
        "with administrator privileges\n"
        "end run"
    )
    recovery = _run(
        [
            "/usr/bin/osascript",
            "-e",
            recovery_script,
            str(app_path.resolve()),
            str(failed.resolve()),
            str(invalid.resolve()),
        ],
        timeout=900,
    )
    if recovery.returncode != 0 or not _macos_application_is_trusted(app_path):
        logger.critical("macOS 恢复快照验签失败，且未能放回原官方 app")
    return False


def _launch_macos_application() -> bool:
    result = _run(
        [
            "/usr/bin/open",
            "-a",
            str(_macos_application_path()),
            "--args",
            "--background",
        ],
        timeout=30,
    )
    return result.returncode == 0


def _stop_windows_host_service() -> tuple[bool, bool]:
    """停止并等待主机服务；返回（成功，停止前需要恢复运行）。"""

    query_before = _run(["sc.exe", "query", "PartyOpsHost"], timeout=10)
    if query_before.returncode == 0 and re.search(
        r"STATE\s*:\s*1\b",
        f"{query_before.stdout}\n{query_before.stderr}",
        re.IGNORECASE,
    ):
        return True, False
    service_executable = Path(sys.executable).resolve().with_name("PartyOpsService.exe")
    if service_executable.is_file():
        result = _run([str(service_executable), "--wait=60", "stop"], timeout=75)
        return result.returncode == 0, result.returncode == 0
    result = _run(["sc.exe", "stop", "PartyOpsHost"], timeout=60)
    if result.returncode == 1062:
        return True, False
    if result.returncode != 0:
        return False, False
    if os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return True, True
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        query = _run(["sc.exe", "query", "PartyOpsHost"], timeout=10)
        if query.returncode == 0 and re.search(
            r"STATE\s*:\s*1\b",
            f"{query.stdout}\n{query.stderr}",
            re.IGNORECASE,
        ):
            return True, True
        time.sleep(1)
    return False, True


def _start_windows_host_service_after_update() -> bool:
    """启动升级后的主机服务；1056 表示安装器已先一步启动成功。"""

    result = _run(["sc.exe", "start", "PartyOpsHost"], timeout=60)
    return result.returncode in {0, 1056}


def _execute_windows_host_update(
    run_id: str,
    package_path: Path,
    manifest: dict,
) -> bool:
    """Windows 平台适配器：安装器升级、服务健康检查和安装器级回滚。"""

    settings = get_settings()
    lock_path = _update_lock_path(settings.data_dir)
    if not _acquire_update_lock(lock_path):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="已有更新事务占用本机；本次重复任务已安全终止，请等待后重试",
        )
        return False
    backup_root: Path | None = None
    database_backup: Path | None = None
    attachments_backup: Path | None = None
    archives_backup: Path | None = None
    installer_cache = _windows_installer_cache()
    current_installer = installer_cache / "current.exe"
    rollback_installer = installer_cache / "rollback.exe"
    mutation_started = False
    service_stopped = False
    try:
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=10,
            message="正在验证 Windows 制品与回滚能力",
        )
        installer_cache.mkdir(parents=True, exist_ok=True)
        if not _verify_cached_rollback_artifact(current_installer):
            raise RuntimeError(
                "WINDOWS_UPGRADE_ROLLBACK_INVALID：当前版本安装器缓存缺失或校验失败，拒绝不可回滚升级"
            )
        backup_root = _secure_update_backup_root(run_id)
        database_backup = backup_root / "partyops.db"
        attachments_backup = backup_root / "attachments"
        archives_backup = backup_root / "archives"
        _cache_verified_rollback_artifact(current_installer, rollback_installer)
        with tempfile.TemporaryDirectory(
            prefix="partyops-update-", dir=backup_root
        ) as temporary:
            platform_name = _manifest_platform_name(manifest)
            architecture = _architecture()
            artifact = _select_artifact(
                package_path,
                manifest,
                architecture,
                Path(temporary) / "PartyOps-update.exe",
                platform_name,
            )
            _ensure_update_snapshot_space(backup_root, settings)
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=25,
                message="正在停止 Windows 主机服务并创建一致快照",
            )
            stopped, service_stopped = _stop_windows_host_service()
            if not stopped:
                raise RuntimeError(
                    "WINDOWS_UPGRADE_SERVICE_STOP_FAILED：主机服务未能安全停止"
                )
            if settings.database_path.exists():
                _online_backup_database(settings.database_path, database_backup)
            if settings.attachments_dir.is_dir():
                _snapshot_managed_tree(
                    settings.attachments_dir, attachments_backup, settings.data_dir
                )
            if settings.archives_dir.is_dir():
                _snapshot_managed_tree(
                    settings.archives_dir, archives_backup, settings.data_dir
                )
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=35,
                message="一致快照完成，正在安装 Windows 更新",
            )
            mutation_started = True
            if not _run_windows_installer(artifact, service_handoff=True):
                raise RuntimeError("Windows 安装器拒绝更新制品")
            if not _verify_cached_rollback_artifact(
                current_installer
            ) or not hmac.compare_digest(_hash(current_installer), _hash(artifact)):
                raise RuntimeError(
                    "WINDOWS_UPGRADE_CACHE_VERIFY_FAILED：新版安装器未能建立可信回滚缓存"
                )
        if not _start_windows_host_service_after_update():
            raise RuntimeError(
                "WINDOWS_UPGRADE_SERVICE_START_FAILED：升级后主机服务未能启动"
            )
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=80,
            message="正在执行数据库迁移和健康检查",
        )
        target_version = str(manifest.get("version") or "").strip() or None
        if not _wait_for_health(target_version, 90):
            raise RuntimeError("Windows 升级后健康检查未通过")
        with db_runtime.session_factory() as db:
            run = db.get(UpdateRun, run_id)
            package = db.get(UpdatePackage, run.package_id) if run else None
            if run:
                run.status = UpdateStatus.COMPLETED
                run.progress = 100
                run.message = (
                    "Windows 主机升级完成；请在方便时重启一次 Windows，"
                    "以完成更新服务组件替换"
                )
                run.completed_at = utcnow()
            if package:
                package.status = UpdateStatus.COMPLETED
                _queue_device_updates(package, db)
            db.commit()
        rollback_installer.unlink(missing_ok=True)
        _rollback_digest_path(rollback_installer).unlink(missing_ok=True)
        _remove_secure_update_transaction(backup_root)
        return True
    except Exception:
        logger.exception("Windows 主机升级失败，诊断编号=%s", run_id)
        if not mutation_started:
            restarted = (
                not service_stopped or _start_windows_host_service_after_update()
            )
            rollback_installer.unlink(missing_ok=True)
            _rollback_digest_path(rollback_installer).unlink(missing_ok=True)
            _remove_secure_update_transaction(backup_root)
            _set_run(
                run_id,
                status=UpdateStatus.FAILED,
                progress=0,
                message=(
                    f"Windows 升级在修改程序前停止，原版本保持不变并继续运行（诊断编号 {run_id[:8]}）"
                    if restarted
                    else f"Windows 升级未修改程序，但原服务恢复失败（诊断编号 {run_id[:8]}）"
                ),
            )
            return False
        _run(["sc.exe", "stop", "PartyOpsHost"], timeout=60)
        program_restored = False
        try:
            program_restored = _verify_cached_rollback_artifact(
                rollback_installer
            ) and _run_windows_installer(rollback_installer, service_handoff=True)
            if database_backup is not None and database_backup.exists():
                _restore_database_snapshot(database_backup, settings.database_path)
            if attachments_backup is not None:
                _restore_managed_tree(
                    attachments_backup, settings.attachments_dir, settings.data_dir
                )
            if archives_backup is not None:
                _restore_managed_tree(
                    archives_backup, settings.archives_dir, settings.data_dir
                )
            if program_restored:
                _cache_verified_rollback_artifact(rollback_installer, current_installer)
                program_restored = _start_windows_host_service_after_update()
                previous_version = (
                    str(getattr(settings, "app_version", "")).strip() or None
                )
                program_restored = program_restored and _wait_for_health(
                    previous_version, 60
                )
                if program_restored:
                    rollback_installer.unlink(missing_ok=True)
                    _rollback_digest_path(rollback_installer).unlink(missing_ok=True)
                else:
                    _run(["sc.exe", "stop", "PartyOpsHost"], timeout=60)
        except Exception:
            logger.exception("Windows 回滚未完成，诊断编号=%s", run_id)
            program_restored = False
        _set_run(
            run_id,
            status=UpdateStatus.ROLLED_BACK
            if program_restored
            else UpdateStatus.FAILED,
            progress=0,
            message=(
                f"Windows 升级未通过，已恢复升级前版本（诊断编号 {run_id[:8]}）"
                if program_restored
                else f"Windows 程序回滚不可用，服务已安全停止（诊断编号 {run_id[:8]}）"
            ),
        )
        if program_restored:
            _remove_secure_update_transaction(backup_root)
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def execute_windows_personal_update(run_id: str) -> bool:
    """在用户确认 UAC 后更新个人模式，程序失败时自动恢复旧安装器。"""

    settings = get_settings()
    if os.name != "nt" or settings.mode != "personal":
        return False
    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        package = db.get(UpdatePackage, run.package_id) if run else None
        if (
            not run
            or run.target_device_id is not None
            or not package
            or package.status not in {UpdateStatus.APPLYING, UpdateStatus.VALIDATED}
        ):
            return False
        source_package = settings.updates_dir / package.filename
        expected_package_hash = package.sha256
    if not source_package.is_file() or not hmac.compare_digest(
        _hash(source_package), expected_package_hash
    ):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="更新包文件缺失或哈希不一致",
        )
        return False
    lock_path = _update_lock_path(settings.data_dir)
    if not _acquire_update_lock(lock_path):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="已有更新事务占用本机；本次重复任务已安全终止，请等待后重试",
        )
        return False
    backup_root: Path | None = None
    installer_cache = _windows_installer_cache()
    current_installer = installer_cache / "current.exe"
    rollback_installer = installer_cache / "personal-rollback.exe"
    mutation_started = False
    try:
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=10,
            message="正在管理员授权边界内重新校验更新包",
        )
        installer_cache.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse_point(installer_cache):
            raise RuntimeError("WINDOWS_UPGRADE_CACHE_LINK：更新缓存目录被替换为链接")
        if not _verify_cached_rollback_artifact(current_installer):
            raise RuntimeError(
                "WINDOWS_UPGRADE_ROLLBACK_INVALID：当前版本安装器缓存缺失，拒绝不可回滚升级"
            )
        backup_root = _secure_update_backup_root(run_id)
        staged_package = backup_root / "verified.partyops-update"
        shutil.copy2(source_package, staged_package)
        if not hmac.compare_digest(_hash(staged_package), expected_package_hash):
            raise RuntimeError(
                "UPDATE_PACKAGE_STAGE_MISMATCH：更新包安全副本哈希不一致"
            )
        manifest = _read_update_manifest(staged_package)
        if _manifest_platform_name(manifest) not in {"windows", "windows7"}:
            raise RuntimeError("当前个人模式无法匹配 Windows 更新制品")
        _cache_verified_rollback_artifact(current_installer, rollback_installer)
        with tempfile.TemporaryDirectory(
            prefix="partyops-personal-update-", dir=backup_root
        ) as temporary:
            artifact = _select_artifact(
                staged_package,
                manifest,
                _architecture(),
                Path(temporary) / "PartyOps-update.exe",
                _manifest_platform_name(manifest),
            )
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=35,
                message="更新包签名与哈希通过，正在安装",
            )
            mutation_started = True
            if not _run_windows_installer(artifact):
                raise RuntimeError("Windows 安装器拒绝个人模式更新")
            if not _verify_cached_rollback_artifact(
                current_installer
            ) or not hmac.compare_digest(_hash(current_installer), _hash(artifact)):
                raise RuntimeError(
                    "WINDOWS_UPGRADE_CACHE_VERIFY_FAILED：新版安装器缓存校验失败"
                )
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=80,
            message="正在等待新版个人模式恢复并完成数据库自检",
        )
        target_version = str(manifest.get("version") or "").strip() or None
        if not _wait_for_health(target_version, 120):
            raise RuntimeError("个人模式更新后健康检查未通过")
        with db_runtime.session_factory() as db:
            run = db.get(UpdateRun, run_id)
            package = db.get(UpdatePackage, run.package_id) if run else None
            if run:
                run.status = UpdateStatus.COMPLETED
                run.progress = 100
                run.message = "个人模式已更新完成"
                run.completed_at = utcnow()
            if package:
                package.status = UpdateStatus.COMPLETED
            db.commit()
        rollback_installer.unlink(missing_ok=True)
        _rollback_digest_path(rollback_installer).unlink(missing_ok=True)
        _remove_secure_update_transaction(backup_root)
        return True
    except Exception:
        logger.exception("Windows 个人模式更新失败，诊断编号=%s", run_id)
        restored = False
        if mutation_started and _verify_cached_rollback_artifact(rollback_installer):
            try:
                restored = _run_windows_installer(rollback_installer)
                previous_version = (
                    str(getattr(settings, "app_version", "")).strip() or None
                )
                restored = restored and _wait_for_health(previous_version, 90)
                if restored:
                    _cache_verified_rollback_artifact(
                        rollback_installer, current_installer
                    )
            except Exception:
                logger.exception("Windows 个人模式程序回滚失败，诊断编号=%s", run_id)
                restored = False
        _set_run(
            run_id,
            status=UpdateStatus.ROLLED_BACK if restored else UpdateStatus.FAILED,
            progress=0,
            message=(
                f"个人模式更新未通过，已恢复上一版本（诊断编号 {run_id[:8]}）"
                if restored
                else f"个人模式更新在安全边界内停止（诊断编号 {run_id[:8]}）"
            ),
        )
        if restored or not mutation_started:
            _remove_secure_update_transaction(backup_root)
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def launch_windows_personal_update(run_id: str) -> bool:
    """从交互桌面触发一次 UAC；高权限进程会重新验证签名而非信任调用方。"""

    if os.name != "nt" or not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class ShellExecuteInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", wintypes.LPVOID),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve().with_name("PartyOpsUpdater.exe")
            arguments = subprocess.list2cmdline(["--personal-run-id", run_id])
        else:
            executable = Path(sys.executable).resolve()
            arguments = subprocess.list2cmdline(
                ["-m", "app.update_executor", "--personal-run-id", run_id]
            )
        if not executable.is_file():
            raise FileNotFoundError(executable)
        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = str(executable)
        info.lpParameters = arguments
        info.lpDirectory = str(executable.parent)
        info.nShow = 0
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):  # type: ignore[attr-defined]
            raise ctypes.WinError()
        if info.hProcess:
            ctypes.windll.kernel32.CloseHandle(info.hProcess)  # type: ignore[attr-defined]
        return True
    except (OSError, ValueError) as exc:
        logger.exception("个人模式更新管理员授权未完成，诊断编号=%s", run_id)
        policy_blocked = (
            getattr(exc, "winerror", None) == 786 or getattr(exc, "errno", None) == 786
        )
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message=(
                "[ADMIN_POLICY_BLOCKED] Windows 组织策略阻止了 PartyOps 更新器。"
                "程序和数据均未修改；请让单位电脑管理员允许安装目录中的 "
                "PartyOpsUpdater.exe 后重试，系统不会绕过安全策略。"
                if policy_blocked
                else "管理员授权未完成，程序和数据均未修改；可重新点击更新。"
            ),
        )
        return False


def _linux_native_version(platform_name: str) -> str:
    if platform_name == "linux-rpm":
        return _partyops_version_from_native(_installed_rpm_version(), "rpm")
    if platform_name in {"linux-deb", "uos"}:
        return _partyops_version_from_native(_installed_package_version(), "deb")
    return ""


def _complete_personal_update_run(run_id: str, *, message: str) -> None:
    """由未提权的个人模式协调器提交业务数据库状态。"""

    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        package = db.get(UpdatePackage, run.package_id) if run else None
        if run:
            run.status = UpdateStatus.COMPLETED
            run.progress = 100
            run.message = message
            run.completed_at = utcnow()
        if package:
            package.status = UpdateStatus.COMPLETED
        db.commit()


def _record_restored_personal_update_run(
    run_id: str,
    *,
    package_id: str,
    created_by: str,
    message: str,
) -> None:
    """升级前备份早于任务记录；恢复后重建一条可审计的回滚结果。"""

    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        if run is None:
            run = UpdateRun(
                id=run_id,
                package_id=package_id,
                target_device_id=None,
                created_by=created_by,
            )
            db.add(run)
        run.status = UpdateStatus.ROLLED_BACK
        run.progress = 0
        run.message = message
        run.completed_at = utcnow()
        package = db.get(UpdatePackage, package_id)
        if package:
            package.status = UpdateStatus.VALIDATED
        db.commit()


def _restart_linux_personal_runtime(
    desktop_uid: int | None = None,
) -> tuple[bool, subprocess.Popen | None]:
    """原生包覆盖后以原桌面账号恢复回环个人进程并等待真实健康端点。"""

    settings = get_settings()
    # root 协调器降权启动桌面进程时不能把自身完整环境交给普通用户；只保留
    # 运行时确实需要的区域设置和固定搜索路径，其余 PARTYOPS 配置在下方重建。
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
        if os.environ.get(key)
    }
    environment.update(
        {
            "PARTYOPS_MODE": "personal",
            "PARTYOPS_ENVIRONMENT": "production",
            "PARTYOPS_DATA_DIR": str(settings.data_dir),
            "PARTYOPS_HOST": "127.0.0.1",
            "PARTYOPS_BIND_HOST": "127.0.0.1",
            "PARTYOPS_ADVERTISE_HOST": "127.0.0.1",
            "PARTYOPS_PORT": str(settings.port),
            "PARTYOPS_AGENT_PORT": str(settings.agent_port),
            "PARTYOPS_TLS_ENABLED": "false",
            "PARTYOPS_STRICT_SQLITE": "true",
        }
    )
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve().with_name("partyops")
        command = [str(executable)]
    else:
        command = [sys.executable, "-m", "app.main"]
    if not Path(command[0]).is_file():
        return False, None
    if desktop_uid is not None:
        if desktop_uid <= 0 or not hasattr(os, "geteuid") or os.geteuid() != 0:
            return False, None
        import pwd

        try:
            account = pwd.getpwuid(desktop_uid)
        except KeyError:
            return False, None
        runuser = shutil.which("runuser")
        if not runuser:
            return False, None
        environment.update(
            {
                "HOME": account.pw_dir,
                "USER": account.pw_name,
                "LOGNAME": account.pw_name,
            }
        )
        command = [runuser, "-u", account.pw_name, "--", *command]
    log_path = settings.data_dir / "launcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_handle:
        if desktop_uid is not None:
            os.fchown(log_handle.fileno(), desktop_uid, account.pw_gid)
        process = subprocess.Popen(  # noqa: S603 - 仅启动同一受保护安装目录内的固定程序。
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return _wait_for_health(None, 90), process


def _pkexec_desktop_uid() -> int:
    """只接受 pkexec 注入的原桌面 UID，禁止 root 事务猜测目标账号。"""

    raw = os.getenv("PKEXEC_UID", "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise RuntimeError("Linux 个人更新缺少可信的桌面账号上下文")
    return int(raw)


def _personal_transaction_file(path: Path, root: Path, label: str) -> Path:
    """把特权事务输入约束在已验证的个人数据根内，并拒绝链接替换。"""

    if not path.is_absolute() or _is_link_or_reparse_point(path):
        raise RuntimeError(f"{label}路径无效")
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise RuntimeError(f"{label}不属于当前个人数据目录")
    return resolved


def _restore_personal_database_as_user(backup_path: Path, desktop_uid: int) -> None:
    """root 恢复数据库后还原桌面账号所有权，避免旧版进程被 root 文件阻断。"""

    settings = get_settings()
    database = settings.database_path
    try:
        original = database.stat()
        database_gid = int(original.st_gid)
        database_mode = stat.S_IMODE(original.st_mode) or 0o600
    except FileNotFoundError:
        import pwd

        account = pwd.getpwuid(desktop_uid)
        database_gid = account.pw_gid
        database_mode = 0o600
    restore_database_from_upgrade_backup(backup_path)
    os.chown(database, desktop_uid, database_gid)
    database.chmod(database_mode & 0o660 or 0o600)


def execute_linux_personal_root_transaction(
    run_id: str,
    package_path: Path,
    backup_path: Path,
) -> int:
    """单次 root 事务完成安装、健康确认以及必要的程序和数据库回滚。

    全局包锁从读取受信制品前一直持有到提交或回滚结束。外层用户协调器
    只在本函数退出后更新业务任务状态，因此 root 不会在个人 SQLite 中留下
    root 所有的 WAL/SHM 文件。
    """

    if (
        os.name == "nt"
        or not sys.platform.startswith("linux")
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
        or not UPDATE_RUN_ID_PATTERN.fullmatch(run_id)
    ):
        return LINUX_PERSONAL_TRANSACTION_FAILED
    lock_path = _update_lock_path(Path("/var/lib/partyops"))
    if not _acquire_update_lock(lock_path):
        logger.error("Linux 个人更新已有全局原生包事务运行")
        return LINUX_PERSONAL_TRANSACTION_FAILED
    process: subprocess.Popen | None = None
    desktop_uid: int | None = None
    backup: Path | None = None
    platform_name = ""
    target_version = ""
    try:
        settings = get_settings()
        desktop_uid = _pkexec_desktop_uid()
        data_metadata = settings.data_dir.stat()
        if int(data_metadata.st_uid) != desktop_uid:
            raise RuntimeError("个人数据目录所有者与授权桌面账号不一致")
        package = _personal_transaction_file(
            package_path, settings.updates_dir, "个人更新包"
        )
        backup = _personal_transaction_file(
            backup_path, settings.backups_dir, "升级前备份"
        )
        verify_backup(backup)
        manifest = _read_update_manifest(package)
        if not _verify_manifest_signature(manifest):
            raise RuntimeError("个人更新包发布签名无效")
        _assert_update_not_downgrade(manifest)
        platform_name = _manifest_platform_name(manifest)
        if platform_name not in {"linux-deb", "linux-rpm", "uos"}:
            raise RuntimeError("个人更新包与 Linux 原生包格式不匹配")
        previous_version = _linux_native_version(platform_name)
        target_version = str(manifest.get("version", "")).strip()
        if previous_version == target_version:
            # 双击或重复调度同一版本时没有可回滚的包变化。只恢复并核对当前
            # 个人进程，禁止伪造一份“上一版本”缓存后误降级。
            healthy, process = _restart_linux_personal_runtime(desktop_uid)
            if healthy:
                return LINUX_PERSONAL_TRANSACTION_COMPLETED
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            process = None
            return LINUX_PERSONAL_TRANSACTION_FAILED
        installed = install_device_package(
            package,
            retain_personal_rollback=run_id,
            _lock_already_held=True,
        )
        current_version = _linux_native_version(platform_name)
        if not installed:
            return (
                LINUX_PERSONAL_TRANSACTION_ROLLED_BACK
                if previous_version and current_version == previous_version
                else LINUX_PERSONAL_TRANSACTION_FAILED
            )
        if current_version != target_version:
            raise RuntimeError("包管理器返回成功，但个人更新版本回读不一致")
        healthy, process = _restart_linux_personal_runtime(desktop_uid)
        if healthy:
            try:
                _discard_personal_native_rollback(run_id)
            except (OSError, RuntimeError):
                # 更新已经通过真实健康检查，旧缓存清理失败只保留诊断，不能
                # 因清理动作反向触发一次不必要的系统包降级。
                logger.warning("个人更新旧版缓存未能清理，诊断编号=%s", run_id)
            return LINUX_PERSONAL_TRANSACTION_COMPLETED
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        process = None
        if not _rollback_linux_personal_package_locked(run_id):
            raise RuntimeError("个人更新健康失败，原生包回滚未能完成")
        _restore_personal_database_as_user(backup, desktop_uid)
        restored_health, process = _restart_linux_personal_runtime(desktop_uid)
        if not restored_health:
            raise RuntimeError("个人更新回滚后旧版运行时未能恢复")
        return LINUX_PERSONAL_TRANSACTION_ROLLED_BACK
    except (
        OSError,
        KeyError,
        ProblemException,
        RuntimeError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
    ):
        logger.exception("Linux 个人模式 root 事务失败，诊断编号=%s", run_id)
        if (
            desktop_uid is not None
            and backup is not None
            and platform_name
            and target_version
        ):
            try:
                if _linux_native_version(
                    platform_name
                ) == target_version and _rollback_linux_personal_package_locked(run_id):
                    _restore_personal_database_as_user(backup, desktop_uid)
                    restored, process = _restart_linux_personal_runtime(desktop_uid)
                    if restored:
                        return LINUX_PERSONAL_TRANSACTION_ROLLED_BACK
            except (OSError, RuntimeError, ValueError):
                logger.exception(
                    "Linux 个人模式异常路径自动回滚失败，诊断编号=%s", run_id
                )
        return LINUX_PERSONAL_TRANSACTION_FAILED
    finally:
        lock_path.unlink(missing_ok=True)


def execute_linux_personal_update(run_id: str) -> bool:
    """用户态协调器调用固定 polkit helper，并在升级后恢复个人进程。"""

    if os.name == "nt" or not sys.platform.startswith("linux"):
        return False
    if not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        return False
    settings = get_settings()
    if settings.mode != "personal":
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="个人模式更新上下文无效，程序和数据均未修改",
        )
        return False
    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        package = db.get(UpdatePackage, run.package_id) if run else None
        if (
            run is None
            or run.target_device_id is not None
            or run.status != UpdateStatus.APPLYING
            or package is None
            or not package.signature_valid
        ):
            return False
        package_path = settings.updates_dir / package.filename
        expected_hash = package.sha256
        package_id = package.id
        created_by = run.created_by
        backup = db.scalar(
            select(BackupRun)
            .where(
                BackupRun.kind == "pre-upgrade",
                BackupRun.status == "completed",
            )
            .order_by(BackupRun.completed_at.desc(), BackupRun.created_at.desc())
        )
        backup_path = settings.backups_dir / backup.filename if backup else None
    if not package_path.is_file() or not hmac.compare_digest(
        _hash(package_path), expected_hash
    ):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="更新包缺失或哈希不一致，程序和数据均未修改",
        )
        return False
    if backup_path is None or not backup_path.is_file():
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="升级前备份缺失，程序和数据均未修改",
        )
        return False
    try:
        if getattr(sys, "frozen", False):
            updater = Path(sys.executable).resolve()
            privileged_prefix = ["pkexec", str(updater)]
        else:
            privileged_prefix = [
                "pkexec",
                sys.executable,
                "-m",
                "app.update_executor",
            ]
        command = [
            *privileged_prefix,
            "--linux-personal-transaction",
            run_id,
            "--personal-package",
            str(package_path),
            "--personal-backup",
            str(backup_path),
            "--personal-data-dir",
            str(settings.data_dir),
            "--personal-port",
            str(settings.port),
        ]
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=30,
            message="等待系统管理员授权并安装已签名更新",
        )
        # 独立协调器自身也不能在包管理器停止旧主程序后继续持有 SQLite/WAL
        # 句柄，否则新版本首次迁移与失败恢复会被旧连接干扰。
        db_runtime.dispose()
        result = _run(command, timeout=1_200)
        if result.returncode == LINUX_PERSONAL_TRANSACTION_COMPLETED:
            _complete_personal_update_run(
                run_id,
                message="个人模式升级完成；程序已自动恢复，无需重新下载安装",
            )
            return True
        if result.returncode == LINUX_PERSONAL_TRANSACTION_ROLLED_BACK:
            _record_restored_personal_update_run(
                run_id,
                package_id=package_id,
                created_by=created_by,
                message="更新未通过，已在单次管理员事务内恢复上一版本和升级前数据库",
            )
            return False
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="管理员授权未完成或更新事务未通过；请打开更新日志",
        )
        return False
    except (OSError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile):
        logger.exception("Linux 个人模式更新失败，诊断编号=%s", run_id)
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message=f"个人模式更新未通过（诊断编号 {run_id[:8]}），请打开更新日志",
        )
        return False


def launch_linux_personal_update(run_id: str) -> bool:
    """启动独立用户态协调器，使主程序被包管理器停止后更新仍能收尾。"""

    if os.name == "nt" or not sys.platform.startswith("linux"):
        return False
    if not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        return False
    try:
        if getattr(sys, "frozen", False):
            updater = Path(sys.executable).resolve().with_name("partyops-updater")
            command = [str(updater), "--linux-personal-run-id", run_id]
        else:
            command = [
                sys.executable,
                "-m",
                "app.update_executor",
                "--linux-personal-run-id",
                run_id,
            ]
        if not Path(command[0]).is_file():
            raise FileNotFoundError(command[0])
        subprocess.Popen(  # noqa: S603 - 仅启动同包内固定更新协调器。
            command,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        logger.exception("Linux 个人模式更新协调器未能启动，诊断编号=%s", run_id)
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="更新协调器未能启动，程序和数据均未修改",
        )
        return False
    return True


def execute_macos_update(run_id: str) -> bool:
    """在独立 onefile helper 中执行 macOS PKG 安装、健康确认与完整回滚。"""

    if sys.platform != "darwin" or not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        return False
    settings = get_settings()
    if settings.mode not in {"host", "personal"}:
        return False
    with db_runtime.session_factory() as db:
        run = db.get(UpdateRun, run_id)
        package = db.get(UpdatePackage, run.package_id) if run else None
        backup = db.scalar(
            select(BackupRun)
            .where(
                BackupRun.kind == "pre-upgrade",
                BackupRun.status == "completed",
            )
            .order_by(BackupRun.completed_at.desc(), BackupRun.created_at.desc())
        )
        if (
            run is None
            or run.target_device_id is not None
            or run.status != UpdateStatus.APPLYING
            or package is None
            or not package.signature_valid
        ):
            return False
        package_path = settings.updates_dir / package.filename
        expected_hash = package.sha256
        package_id = package.id
        created_by = run.created_by
        backup_path = settings.backups_dir / backup.filename if backup else None
    if (
        not package_path.is_file()
        or not hmac.compare_digest(_hash(package_path), expected_hash)
        or backup_path is None
        or not backup_path.is_file()
    ):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="macOS 更新包或升级前备份缺失，程序和数据均未修改",
        )
        return False

    lock_path = _update_lock_path(settings.data_dir)
    if not _acquire_update_lock(lock_path):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="已有 macOS 更新事务正在运行，请稍后重试",
        )
        return False
    transaction: Path | None = None
    app_path = _macos_application_path()
    previous_version = ""
    mutation_started = False
    runtime_stopped = False
    try:
        transaction = _secure_update_backup_root(run_id)
        staged_update = transaction / "verified.partyops-update"
        _cache_verified_rollback_artifact(package_path, staged_update)
        manifest = _read_update_manifest(staged_update)
        if not _verify_manifest_signature(manifest):
            raise RuntimeError("macOS 更新包发布签名无效")
        _assert_update_not_downgrade(manifest)
        if _manifest_platform_name(manifest) != "macos":
            raise RuntimeError("更新包与当前 macOS 平台不匹配")
        target_version = str(manifest.get("version") or "").strip()
        previous_version = _macos_bundle_version(app_path)
        if not previous_version or not _macos_application_is_trusted(app_path):
            raise RuntimeError("未找到可验签、可回滚的 PartyOps.app 正式安装")
        with tempfile.TemporaryDirectory(prefix="payload-", dir=transaction) as temporary:
            artifact = _select_artifact(
                staged_update,
                manifest,
                _architecture(),
                Path(temporary) / "PartyOps-update.pkg",
                "macos",
            )
            signature = _run(
                ["/usr/sbin/pkgutil", "--check-signature", str(artifact)], timeout=60
            )
            if signature.returncode != 0 or "Developer ID Installer" not in (
                signature.stdout + signature.stderr
            ):
                raise RuntimeError("macOS PKG 没有有效的 Developer ID Installer 签名")
            assessment = _run(
                ["/usr/sbin/spctl", "--assess", "--type", "install", str(artifact)],
                timeout=60,
            )
            if assessment.returncode != 0:
                raise RuntimeError("macOS Gatekeeper 拒绝更新安装包")
            if previous_version == target_version:
                _complete_personal_update_run(
                    run_id, message="macOS 已是目标版本，无需重复安装"
                )
                _remove_secure_update_transaction(transaction)
                transaction = None
                return True
            snapshot = transaction / "PartyOps-previous.app"
            failed_app = transaction / "PartyOps-failed.app"
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=25,
                message="正在创建 macOS 应用与数据回滚快照",
            )
            copy_result = _run(
                [
                    "/usr/bin/ditto",
                    "--rsrc",
                    "--extattr",
                    "--acl",
                    str(app_path),
                    str(snapshot),
                ],
                timeout=900,
            )
            if (
                copy_result.returncode != 0
                or _macos_bundle_version(snapshot) != previous_version
                or not _macos_application_is_trusted(snapshot)
            ):
                raise RuntimeError("macOS 旧版应用快照创建或回读失败")
            db_runtime.dispose()
            runtime_stopped = _stop_macos_runtime(app_path, settings.port)
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=45,
                message="等待 macOS 管理员确认并安装更新",
            )
            mutation_started = True
            if not _run_macos_privileged_installer(artifact):
                raise RuntimeError("macOS 管理员授权未完成或系统安装器拒绝更新")
            if (
                _macos_bundle_version(app_path) != target_version
                or not _macos_application_is_trusted(app_path)
            ):
                raise RuntimeError("macOS 安装器返回成功，但应用版本或签名回读不一致")
            if not _launch_macos_application() or not _wait_for_health(target_version, 180):
                raise RuntimeError("macOS 新版应用未通过真实健康检查")

        db_runtime.rebuild()
        _set_run(
            run_id,
            status=UpdateStatus.COMPLETED,
            progress=100,
            message="macOS 应用已更新完成",
        )
        _remove_secure_update_transaction(transaction)
        transaction = None
        return True
    except Exception:
        logger.exception("macOS 更新失败，诊断编号=%s", run_id)
        restored = False
        if transaction is not None and mutation_started and previous_version:
            try:
                _stop_macos_runtime(app_path, settings.port)
                snapshot = transaction / "PartyOps-previous.app"
                failed_app = transaction / "PartyOps-failed.app"
                restored = _restore_macos_application(snapshot, app_path, failed_app)
                if restored:
                    db_runtime.dispose()
                    restore_database_from_upgrade_backup(backup_path)
                    restored = (
                        _macos_bundle_version(app_path) == previous_version
                        and _launch_macos_application()
                        and _wait_for_health(previous_version, 180)
                    )
                    db_runtime.rebuild()
            except Exception:
                logger.exception("macOS 自动回滚失败，诊断编号=%s", run_id)
                restored = False
        elif runtime_stopped:
            restored = _launch_macos_application() and _wait_for_health(
                previous_version or None, 90
            )
        if restored:
            _record_restored_personal_update_run(
                run_id,
                package_id=package_id,
                created_by=created_by,
                message=f"macOS 更新未通过，已恢复上一版本（诊断编号 {run_id[:8]}）",
            )
            _remove_secure_update_transaction(transaction)
            transaction = None
        else:
            _set_run(
                run_id,
                status=UpdateStatus.FAILED,
                progress=0,
                message=f"macOS 更新在安全边界内停止（诊断编号 {run_id[:8]}）",
            )
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def launch_macos_update(run_id: str) -> bool:
    """从业务进程启动独立 onefile helper，允许替换当前 `.app` 后继续回滚。"""

    if sys.platform != "darwin" or not UPDATE_RUN_ID_PATTERN.fullmatch(run_id):
        return False
    updater = Path(sys.executable).resolve().with_name("partyops-updater")
    if not updater.is_file() or updater.is_symlink():
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="macOS 更新助手缺失，程序和数据均未修改",
        )
        return False
    settings = get_settings()
    log_path = settings.data_dir / "updater.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("ab") as log:
            subprocess.Popen(  # noqa: S603 - 固定为同一已签名 app 内的更新 helper。
                [str(updater), "--macos-run-id", run_id],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError:
        logger.exception("macOS 更新助手未能启动，诊断编号=%s", run_id)
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="macOS 更新助手未能启动，程序和数据均未修改",
        )
        return False
    return True


def install_macos_device_package(package_path: Path) -> bool:
    """为 macOS 协同 Agent 事务替换 app；业务数据和协同配置始终留在用户目录。"""

    if (
        sys.platform != "darwin"
        or not package_path.is_file()
        or package_path.is_symlink()
    ):
        return False
    transaction_id = secrets.token_hex(16)
    lock_path = _update_lock_path(Path.home())
    if not _acquire_update_lock(lock_path):
        return False
    transaction: Path | None = None
    app_path = _macos_application_path()
    previous_version = ""
    mutation_started = False
    try:
        transaction = _secure_update_backup_root(transaction_id)
        staged_update = transaction / "verified.partyops-update"
        _cache_verified_rollback_artifact(package_path, staged_update)
        manifest = _read_update_manifest(staged_update)
        if not _verify_manifest_signature(manifest):
            raise RuntimeError("macOS 协同更新包发布签名无效")
        _assert_update_not_downgrade(manifest)
        if _manifest_platform_name(manifest) != "macos":
            raise RuntimeError("协同更新包与当前 macOS 平台不匹配")
        target_version = str(manifest.get("version") or "").strip()
        previous_version = _macos_bundle_version(app_path)
        if not previous_version or not _macos_application_is_trusted(app_path):
            raise RuntimeError("当前 PartyOps.app 不是可验签、可回滚的正式安装")
        with tempfile.TemporaryDirectory(prefix="payload-", dir=transaction) as temporary:
            artifact = _select_artifact(
                staged_update,
                manifest,
                _architecture(),
                Path(temporary) / "PartyOps-update.pkg",
                "macos",
            )
            signature = _run(
                ["/usr/sbin/pkgutil", "--check-signature", str(artifact)], timeout=60
            )
            if signature.returncode != 0 or "Developer ID Installer" not in (
                signature.stdout + signature.stderr
            ):
                raise RuntimeError("macOS 协同 PKG 没有有效的 Developer ID Installer 签名")
            assessment = _run(
                ["/usr/sbin/spctl", "--assess", "--type", "install", str(artifact)],
                timeout=60,
            )
            if assessment.returncode != 0:
                raise RuntimeError("macOS Gatekeeper 拒绝协同更新安装包")
            if previous_version == target_version:
                _remove_secure_update_transaction(transaction)
                transaction = None
                return True
            snapshot = transaction / "PartyOps-previous.app"
            copy_result = _run(
                [
                    "/usr/bin/ditto",
                    "--rsrc",
                    "--extattr",
                    "--acl",
                    str(app_path),
                    str(snapshot),
                ],
                timeout=900,
            )
            if (
                copy_result.returncode != 0
                or _macos_bundle_version(snapshot) != previous_version
                or not _macos_application_is_trusted(snapshot)
            ):
                raise RuntimeError("macOS 协同旧版 app 快照未通过验签")
            mutation_started = True
            if not _run_macos_privileged_installer(artifact):
                raise RuntimeError("macOS 管理员授权未完成或系统安装器拒绝协同更新")
            if (
                _macos_bundle_version(app_path) != target_version
                or not _macos_application_is_trusted(app_path)
            ):
                raise RuntimeError("macOS 协同更新后的版本或签名回读不一致")
        _remove_secure_update_transaction(transaction)
        transaction = None
        return True
    except Exception:
        logger.exception("macOS 协同设备更新失败")
        if transaction is not None and mutation_started and previous_version:
            snapshot = transaction / "PartyOps-previous.app"
            failed = transaction / "PartyOps-failed.app"
            if _restore_macos_application(snapshot, app_path, failed):
                _remove_secure_update_transaction(transaction)
                transaction = None
        elif transaction is not None:
            _remove_secure_update_transaction(transaction)
            transaction = None
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
        if not package or package.status not in {
            UpdateStatus.APPLYING,
            UpdateStatus.VALIDATED,
        }:
            return False
        package_path = settings.updates_dir / package.filename
        manifest = {}
    if not package_path.is_file() or _hash(package_path) != package.sha256:
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="更新包文件缺失或哈希不一致",
        )
        return False
    try:
        manifest = _read_update_manifest(package_path)
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile):
        _set_run(
            run_id, status=UpdateStatus.FAILED, progress=0, message="更新包清单损坏"
        )
        return False
    try:
        platform_name = _manifest_platform_name(manifest)
    except (OSError, RuntimeError, TypeError, ValueError):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="当前主机无法匹配更新包平台，未修改程序",
        )
        return False
    if platform_name in {"windows", "windows7"}:
        if not _manifest_has_windows_artifact(manifest):
            _set_run(
                run_id,
                status=UpdateStatus.FAILED,
                progress=0,
                message="更新包不包含当前 Windows 平台与架构的安装器，未修改程序",
            )
            return False
        return _execute_windows_host_update(run_id, package_path, manifest)
    if platform_name == "linux-rpm":
        return _execute_linux_rpm_host_update(run_id, package_path, manifest)
    if platform_name not in {"linux-deb", "uos"}:
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="更新包不包含当前 Linux 平台与架构的安装器，未修改程序",
        )
        return False
    lock_path = _update_lock_path(settings.data_dir)
    if not _acquire_update_lock(lock_path):
        _set_run(
            run_id,
            status=UpdateStatus.FAILED,
            progress=0,
            message="已有更新事务占用本机；本次重复任务已安全终止，请等待后重试",
        )
        return False
    backup_root: Path | None = None
    rollback_package: Path | None = None
    database_backup: Path | None = None
    attachments_backup: Path | None = None
    archives_backup: Path | None = None
    mutation_started = False
    service_stopped = False
    try:
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=10,
            message="正在验证 DEB 制品与回滚能力",
        )
        if not _ensure_dpkg_ready():
            raise RuntimeError("系统包管理器存在未完成配置，已停止本次更新")
        backup_root = _secure_update_backup_root(run_id)
        rollback_package = backup_root / "partyops-rollback.deb"
        database_backup = backup_root / "partyops.db"
        attachments_backup = backup_root / "attachments"
        archives_backup = backup_root / "archives"
        _create_installed_package_snapshot(rollback_package)
        with tempfile.TemporaryDirectory(
            prefix="partyops-update-", dir=backup_root
        ) as temporary:
            artifact = _select_artifact(
                package_path,
                manifest,
                _architecture(),
                Path(temporary) / "partyops.deb",
            )
            _ensure_update_snapshot_space(backup_root, settings)
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=25,
                message="正在停止服务并创建一致快照",
            )
            if _run(["systemctl", "stop", "partyops"], timeout=30).returncode != 0:
                raise RuntimeError(
                    "DEB_UPGRADE_SERVICE_STOP_FAILED：主机服务未能安全停止"
                )
            service_stopped = True
            if settings.database_path.exists():
                _online_backup_database(settings.database_path, database_backup)
            # 升级迁移可能涉及材料索引和附件路径；程序、数据库之外保留受管
            # 附件及归档索引快照，失败时可以完整回到升级前状态。
            if settings.attachments_dir.is_dir():
                _snapshot_managed_tree(
                    settings.attachments_dir, attachments_backup, settings.data_dir
                )
            if settings.archives_dir.is_dir():
                _snapshot_managed_tree(
                    settings.archives_dir, archives_backup, settings.data_dir
                )
            _set_run(
                run_id,
                status=UpdateStatus.APPLYING,
                progress=35,
                message="一致快照完成，正在安装 DEB",
            )
            mutation_started = True
            result = _run_linux_package_manager(
                ["dpkg", "--unpack", str(artifact)], timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError("系统安装器拒绝更新制品")
            result = _run_linux_package_manager(
                ["dpkg", "--configure", "partyops"], timeout=300
            )
            if result.returncode != 0:
                raise RuntimeError("系统安装器配置更新失败")
        if _run(["systemctl", "start", "partyops"], timeout=60).returncode != 0:
            raise RuntimeError(
                "DEB_UPGRADE_SERVICE_START_FAILED：升级后主机服务未能启动"
            )
        _set_run(
            run_id,
            status=UpdateStatus.APPLYING,
            progress=80,
            message="正在执行数据库迁移和健康检查",
        )
        target_version = str(manifest.get("version") or "").strip() or None
        if not _wait_for_health(target_version, 60):
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
                    run.message = (
                        f"主机升级完成；{waiting} 台协同电脑将在进入系统时确认更新"
                    )
            db.commit()
        _remove_secure_update_transaction(backup_root)
        return True
    except Exception:
        logger.exception("主机升级失败，诊断编号=%s", run_id)
        if not mutation_started:
            restarted = (
                not service_stopped
                or _run(["systemctl", "start", "partyops"], timeout=60).returncode == 0
            )
            _remove_secure_update_transaction(backup_root)
            _set_run(
                run_id,
                status=UpdateStatus.FAILED,
                progress=0,
                message=(
                    f"DEB 升级在修改程序前停止，原版本保持不变并继续运行（诊断编号 {run_id[:8]}）"
                    if restarted
                    else f"DEB 升级未修改程序，但原服务恢复失败（诊断编号 {run_id[:8]}）"
                ),
            )
            return False
        _run(["systemctl", "stop", "partyops"], timeout=30)
        program_restored = False
        try:
            if rollback_package is not None and rollback_package.is_file():
                unpack = _run_linux_package_manager(
                    ["dpkg", "--unpack", str(rollback_package)], timeout=300
                )
                configure = (
                    _run_linux_package_manager(
                        ["dpkg", "--configure", "-a"], timeout=300
                    )
                    if unpack.returncode == 0
                    else unpack
                )
                program_restored = unpack.returncode == 0 and configure.returncode == 0
            if database_backup is not None and database_backup.exists():
                _restore_database_snapshot(database_backup, settings.database_path)
            if attachments_backup is not None:
                _restore_managed_tree(
                    attachments_backup,
                    settings.attachments_dir,
                    settings.data_dir,
                )
            if archives_backup is not None:
                _restore_managed_tree(
                    archives_backup,
                    settings.archives_dir,
                    settings.data_dir,
                )
            if program_restored:
                program_restored = (
                    _run(["systemctl", "start", "partyops"], timeout=60).returncode == 0
                )
                previous_version = (
                    str(getattr(settings, "app_version", "")).strip() or None
                )
                program_restored = program_restored and _wait_for_health(
                    previous_version, 60
                )
                if not program_restored:
                    _run(["systemctl", "stop", "partyops"], timeout=30)
        except Exception:
            logger.exception("DEB 回滚未完成，诊断编号=%s", run_id)
            program_restored = False
        _set_run(
            run_id,
            status=(
                UpdateStatus.ROLLED_BACK if program_restored else UpdateStatus.FAILED
            ),
            progress=0,
            message=(
                f"升级未通过，已自动恢复升级前版本（诊断编号 {run_id[:8]}）"
                if program_restored
                else f"程序回滚未完成，服务已安全停止（诊断编号 {run_id[:8]}）"
            ),
        )
        if program_restored:
            _remove_secure_update_transaction(backup_root)
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def install_device_package(
    package_path: Path,
    *,
    retain_personal_rollback: str = "",
    _lock_already_held: bool = False,
) -> bool:
    """由桌面 Agent 调用；只安装与当前平台和架构匹配的签名制品。"""

    if not package_path.is_file() or package_path.suffix != ".partyops-update":
        return False
    transaction: Path | None = None
    lock_path: Path | None = None
    try:
        if (
            os.name != "nt"
            and _getenv("PARTYOPS_ENVIRONMENT") != "test"
            and not _lock_already_held
        ):
            lock_path = _update_lock_path(package_path.parent)
            if not _acquire_update_lock(lock_path):
                logger.error("已有原生包更新事务正在运行，拒绝并发安装")
                return False
        architecture = _architecture()
        transaction = _secure_update_backup_root(f"device-{secrets.token_hex(12)}")
        staged_package = transaction / "verified.partyops-update"
        with package_path.open("rb") as source, staged_package.open(
            "xb"
        ) as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
            destination.flush()
            _fsync(destination.fileno())
        with tempfile.TemporaryDirectory(
            prefix="payload-", dir=transaction
        ) as temporary:
            manifest = _read_update_manifest(staged_package)
            platform_name = _manifest_platform_name(manifest)
            extension = (
                ".exe"
                if platform_name in {"windows", "windows7"}
                else ".rpm"
                if platform_name == "linux-rpm"
                else ".deb"
            )
            # 即使目标版本已经安装，也必须先验证发布签名、架构、大小和哈希。
            # 这样重复更新保持幂等，但伪造或损坏的同版本更新包不会被误报成功。
            artifact = _select_artifact(
                staged_package,
                manifest,
                architecture,
                Path(temporary) / f"partyops-update{extension}",
                platform_name,
            )
            if platform_name in {"windows", "windows7"}:
                from . import __version__

                installed_version = __version__
            elif platform_name == "linux-rpm":
                installed_version = _partyops_version_from_native(
                    _installed_rpm_version(), "rpm"
                )
            else:
                installed_version = _partyops_version_from_native(
                    _installed_package_version(), "deb"
                )
            if installed_version == str(manifest.get("version", "")):
                return True
            if platform_name in {"windows", "windows7"}:
                if retain_personal_rollback:
                    raise RuntimeError("Windows 不能使用 Linux 个人模式回滚令牌")
                return _run_windows_installer(artifact)
            if platform_name == "linux-rpm":
                rollback = transaction / "partyops-device-rollback.rpm"
                if not _verify_cached_rollback_artifact(RPM_PACKAGE_CACHE):
                    raise RuntimeError("当前 RPM 回滚缓存缺失，拒绝不可回滚的终端升级")
                _cache_verified_rollback_artifact(RPM_PACKAGE_CACHE, rollback)
                if retain_personal_rollback:
                    _persist_personal_native_rollback(
                        retain_personal_rollback,
                        rollback,
                        platform_name=platform_name,
                        previous_version=installed_version,
                        target_version=str(manifest.get("version", "")),
                    )
                installed = _install_rpm(artifact)
                target = str(manifest.get("version", ""))
                installed = installed and (
                    _partyops_version_from_native(_installed_rpm_version(), "rpm")
                    == target
                )
                if installed:
                    _cache_verified_rollback_artifact(artifact, RPM_PACKAGE_CACHE)
                    return True
                restored = _verify_cached_rollback_artifact(rollback) and _install_rpm(
                    rollback, allow_downgrade=True
                )
                if restored and retain_personal_rollback:
                    _discard_personal_native_rollback(retain_personal_rollback)
                if not restored:
                    transaction = None  # 保留 root 专用事务目录供管理员恢复。
                return False
            rollback = transaction / "partyops-device-rollback.deb"
            _create_installed_package_snapshot(rollback)
            if retain_personal_rollback:
                _persist_personal_native_rollback(
                    retain_personal_rollback,
                    rollback,
                    platform_name=platform_name,
                    previous_version=installed_version,
                    target_version=str(manifest.get("version", "")),
                )
            if not _ensure_dpkg_ready():
                if retain_personal_rollback:
                    _discard_personal_native_rollback(retain_personal_rollback)
                return False
            unpack = _run_linux_package_manager(
                ["dpkg", "--unpack", str(artifact)], timeout=300
            )
            configured = (
                _run_linux_package_manager(
                    ["dpkg", "--configure", "partyops"], timeout=300
                )
                if unpack.returncode == 0
                else unpack
            )
            target = str(manifest.get("version", ""))
            installed = (
                unpack.returncode == 0
                and configured.returncode == 0
                and (
                    _partyops_version_from_native(_installed_package_version(), "deb")
                    == target
                )
            )
            if installed:
                return True
            restore_unpack = _run_linux_package_manager(
                ["dpkg", "--unpack", str(rollback)], timeout=300
            )
            restore_configure = (
                _run_linux_package_manager(["dpkg", "--configure", "-a"], timeout=300)
                if restore_unpack.returncode == 0
                else restore_unpack
            )
            if restore_unpack.returncode != 0 or restore_configure.returncode != 0:
                transaction = None  # 保留 root 专用事务目录供管理员恢复。
            elif retain_personal_rollback:
                _discard_personal_native_rollback(retain_personal_rollback)
            return False
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile):
        logger.exception("协同电脑更新包校验或安装失败")
        return False
    finally:
        _remove_secure_update_transaction(transaction)
        if lock_path is not None:
            lock_path.unlink(missing_ok=True)


def run_daemon(once: bool = False) -> int:
    database_retry_seconds = 3
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
        except OperationalError as exc:
            # 终端模式可能没有主机数据库；更新服务保持空闲，不创建第二份数据库。
            logger.warning(
                "更新服务暂时无法读取数据库，将在 %s 秒后重试：%s",
                database_retry_seconds,
                type(exc).__name__,
            )
            if once:
                return 0
            time.sleep(database_retry_seconds)
            database_retry_seconds = min(database_retry_seconds * 2, 60)
            continue
        database_retry_seconds = 3
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


def _trusted_system_environment_file(path: Path) -> bool:
    """特权更新服务只读取 root 拥有且不可被其他身份写入的普通文件。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not _is_link_or_reparse_point(path)
        and int(getattr(metadata, "st_uid", -1)) == 0
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
    )


def _candidate_host_environments() -> list[dict[str, str]]:
    # root 包管理器不能扫描用户家目录。否则普通用户可伪造更新公钥、数据库
    # 任务和安装包，把任意 DEB/RPM 交给 root 子进程安装。
    candidates = [Path("/etc/partyops/partyops.env")]
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in candidates:
        if not _trusted_system_environment_file(path):
            logger.error("拒绝读取权限或归属不安全的 PartyOps 系统配置：%s", path)
            continue
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
        allowed = resolved == Path("/var/lib/partyops")
        if not allowed or str(resolved) in seen:
            continue
        seen.add(str(resolved))
        sanitized = {
            key: value
            for key, value in values.items()
            if key in PRIVILEGED_UPDATE_ENV_KEYS
        }
        sanitized["PARTYOPS_MODE"] = "host"
        sanitized["PARTYOPS_DATA_DIR"] = str(resolved)
        sanitized["PARTYOPS_ENVIRONMENT"] = "production"
        sanitized["PARTYOPS_STRICT_SQLITE"] = "true"
        results.append(sanitized)
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
                "WHERE target_device_id IS NULL AND status='APPLYING' "
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
            lock_path = _update_lock_path(Path(values["PARTYOPS_DATA_DIR"]))
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
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("PARTYOPS_")
            }
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
    parser.add_argument("--personal-run-id", default="")
    parser.add_argument("--linux-personal-run-id", default="")
    parser.add_argument("--macos-run-id", default="")
    parser.add_argument("--macos-install-package", type=Path)
    parser.add_argument("--linux-personal-transaction", default="")
    parser.add_argument("--personal-package", type=Path)
    parser.add_argument("--personal-backup", type=Path)
    parser.add_argument("--personal-data-dir", type=Path)
    parser.add_argument("--personal-port", type=int)
    parser.add_argument("--windows-system-service", action="store_true")
    parser.add_argument("--install-package", type=Path)
    parser.add_argument("--supervisor", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.personal_run_id:
        raise SystemExit(
            0 if execute_windows_personal_update(args.personal_run_id) else 1
        )
    if args.linux_personal_run_id:
        raise SystemExit(
            0 if execute_linux_personal_update(args.linux_personal_run_id) else 1
        )
    if args.macos_run_id:
        raise SystemExit(0 if execute_macos_update(args.macos_run_id) else 1)
    if args.macos_install_package:
        raise SystemExit(0 if install_macos_device_package(args.macos_install_package) else 1)
    if args.linux_personal_transaction:
        if args.personal_package is None or args.personal_backup is None:
            raise SystemExit(LINUX_PERSONAL_TRANSACTION_FAILED)
        raise SystemExit(
            execute_linux_personal_root_transaction(
                args.linux_personal_transaction,
                args.personal_package,
                args.personal_backup,
            )
        )
    if args.install_package:
        installed = install_device_package(args.install_package)
        raise SystemExit(0 if installed else 1)
    if args.supervisor:
        raise SystemExit(run_supervisor(args.once))
    if args.run_id:
        raise SystemExit(0 if execute_host_update(args.run_id) else 1)
    if args.windows_system_service:
        raise SystemExit(run_daemon(args.once))
    raise SystemExit(run_daemon(args.once))


if __name__ == "__main__":
    main()
