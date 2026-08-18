"""便携版首次配置向导：选择主机或终端，不创建第二份业务数据库。"""

from __future__ import annotations

import argparse
import base64
import getpass
import html
import http.client
import ipaddress
import json
import math
import os
import re
import signal
import secrets
import shlex
import shutil
import socket
import sqlite3
import ssl
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .client_agent import (
    add_shared_root,
    configure_ssl_context,
    create_browser_launch_url,
    device_metadata,
    enroll_device,
    refresh_shared_root_statuses,
    remove_shared_root,
    rename_shared_root,
    scan_and_upload_roots,
    send_device_heartbeat,
    validate_config,
)
from .networking import discover_lan_addresses
from .windows_host_status import (
    CHILD_EXITED,
    DATA_DIR_DENIED,
    HEALTH_TIMEOUT,
    PORT_IN_USE,
    SERVICE_MISSING,
    SERVICE_STOPPED,
    TERMINAL_CODES,
    TLS_INIT_FAILED,
    read_service_status,
    health_payload_ready,
    service_log_path,
    tail_service_log,
    write_service_status,
)
from .startup_diagnostics import public_startup_message


class HostStartupError(ConnectionError, ValueError):
    """带稳定诊断码的主机启动失败。"""

    def __init__(self, code: str, message: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        public_detail = public_startup_message(code, message)
        super().__init__(f"[{code}] {public_detail}")


ADMIN_POLICY_BLOCKED = "ADMIN_POLICY_BLOCKED"


def _windows_policy_blocked(detail: str, returncode: int = 0) -> bool:
    """识别 SRP/AppLocker/WDAC 拒绝执行，不能把组织策略误报成用户取消。"""

    normalized = detail.casefold()
    return returncode == 786 or any(
        marker in normalized
        for marker in (
            "winerror 786",
            "error 786",
            "policy rule",
            "策略规则",
            "已被管理员用策略",
            "管理员用策略规则",
        )
    )


def config_root() -> Path:
    root = (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
        if os.name == "nt"
        else Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "partyops"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def installer_default_data_dir() -> Path:
    """读取安装器预选目录；损坏或相对路径不会影响向导启动。"""

    control_root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
    fallback = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps-Data"
    if os.name != "nt":
        return Path.home() / "PartyOps-数据"
    marker = control_root / "install-data-dir.txt"
    try:
        raw = marker.read_text(encoding="utf-8").strip()
        candidate = Path(raw).expanduser()
        if raw and candidate.is_absolute() and len(raw) <= 1024:
            return candidate
    except OSError:
        pass
    return fallback


def _write_private(path: Path, content: str, mode: int = 0o600) -> None:
    """原子写入本地配置或证书，并按用途设置最小文件权限。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(mode)
    temporary.replace(path)


def _publish_linux_desktop_tool_url(tool: str, url: str) -> Path | None:
    """向 Linux 桌面启动器发布只绑定回环地址的本地工具入口。"""

    if not sys.platform.startswith("linux"):
        return None
    parsed = urllib.parse.urlparse(url)
    if (
        tool not in {"wizard", "shared-root-manager"}
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("本地桌面工具只能发布到 127.0.0.1 临时端口")
    marker = config_root() / f"{tool}.url"
    _write_private(marker, url + "\n")
    return marker


def _clear_linux_desktop_tool_url(marker: Path | None, url: str) -> None:
    """仅清理由当前进程写入的地址，避免并发向导互删状态。"""

    if marker is None:
        return
    try:
        if marker.read_text(encoding="utf-8").strip() == url:
            marker.unlink(missing_ok=True)
    except OSError:
        pass


def write_mode_config(mode: str, *, config_path: Path | None = None) -> Path:
    if mode not in {"host", "personal", "client"}:
        raise ValueError("运行模式必须是 host、personal 或 client")
    path = config_root() / "mode.json"
    payload = {"format_version": 1, "mode": mode}
    if config_path is not None:
        payload["config_path"] = str(config_path.resolve())
    _write_private(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def personal_default_data_dir() -> Path:
    """个人模式默认落在当前账号目录，不需要 SYSTEM 或管理员写权限。"""

    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "PartyOps-个人数据"
    return (
        Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        / "partyops-personal"
    )


def initial_personal_data_dir() -> Path:
    """Windows 首次向导沿用安装器选择，其他平台沿用用户目录默认值。"""

    return (
        installer_default_data_dir() if os.name == "nt" else personal_default_data_dir()
    )


def _write_data_root_marker(data_dir: Path, scope: str) -> None:
    """写入卸载边界标记；卸载器绝不凭一个配置路径直接递归删除。"""

    if scope not in {"host", "personal", "client"}:
        raise ValueError("PartyOps 数据目录范围无效")
    marker_path = data_dir / ".partyops-data-root.json"
    scopes = {scope}
    if marker_path.is_symlink():
        raise ValueError("现有 PartyOps 数据目录标记是链接，请更换空目录")
    if marker_path.is_file():
        try:
            previous = json.loads(marker_path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or (
                previous.get("product") != "PartyOps"
                or previous.get("app_id") != "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A"
            ):
                raise ValueError("所有权不匹配")
            if previous.get("format_version") == 2:
                old_scopes = previous.get("scopes")
                if not isinstance(old_scopes, list) or not old_scopes:
                    raise ValueError("范围列表无效")
                normalized = {str(value) for value in old_scopes}
            elif previous.get("format_version") == 1:
                normalized = {str(previous.get("scope", ""))}
            else:
                raise ValueError("标记版本无效")
            if not normalized.issubset({"host", "personal", "client"}):
                raise ValueError("范围无效")
            scopes.update(normalized)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # 损坏或伪造的旧标记绝不能被“修复”为受信标记。
            raise ValueError("现有 PartyOps 数据目录标记损坏，请更换空目录") from None
    marker = {
        "format_version": 2,
        "product": "PartyOps",
        "app_id": "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A",
        "scopes": sorted(scopes),
    }
    _write_private(
        marker_path,
        json.dumps(marker, ensure_ascii=False, indent=2),
    )


def validate_host_config_selection(host: str, port: int) -> None:
    if host not in {"127.0.0.1", *discover_lan_addresses()}:
        raise ValueError("请选择本机检测到的明确局域网地址")
    if not 1024 <= port <= 65534:
        raise ValueError("主机端口必须在 1024—65534 之间，下一端口用于 Agent 安全通道")


def _is_link_or_reparse_point(path: Path) -> bool:
    """兼容 Python 3.8 检测 NTFS 符号链接、目录联接与其他重解析点。"""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def _assert_path_components_have_no_reparse_points(path: Path) -> None:
    """在解析路径前逐级检查，避免 ``resolve`` 隐藏目录联接。"""

    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if _is_link_or_reparse_point(current):
            raise ValueError("数据目录及其父目录不能是符号链接或目录联接")
        if not current.exists():
            break


def _assert_managed_data_tree_has_no_reparse_points(path: Path) -> None:
    _assert_path_components_have_no_reparse_points(path)
    if not path.exists():
        return
    for current, directories, filenames in os.walk(path, followlinks=False):
        for name in [*directories, *filenames]:
            candidate = Path(current) / name
            if _is_link_or_reparse_point(candidate):
                raise ValueError(f"数据目录包含符号链接或目录联接：{candidate}")


def assert_windows_service_data_path_security(
    data_dir: Path,
    *,
    verify_target: bool,
) -> None:
    """核验 SYSTEM 服务最终数据目录的所有者和写入权限。

    用户可以把 PartyOps 放在任意本地固定磁盘的自建父目录中；这些父目录常会
    继承 ``Authenticated Users: Modify``，不能因此误判为数据目录本身不可用。
    真正由服务使用的最终目录必须关闭这类写权限，并持续拒绝重解析点。
    """

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    import win32security  # type: ignore[import-untyped]

    trusted_sids = {
        "S-1-5-18",  # LocalSystem
        "S-1-5-32-544",  # BUILTIN\Administrators
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
    }
    write_dac = 0x00040000
    write_owner = 0x00080000
    generic_all = 0x10000000
    generic_write = 0x40000000
    file_write_data = 0x00000002
    file_append_data = 0x00000004
    file_write_ea = 0x00000010
    file_delete_child = 0x00000040
    file_write_attributes = 0x00000100
    target_write_mask = (
        write_dac
        | write_owner
        | generic_all
        | generic_write
        | file_write_data
        | file_append_data
        | file_write_ea
        | file_delete_child
        | file_write_attributes
    )
    resolved = data_dir.resolve(strict=True)
    _assert_path_components_have_no_reparse_points(resolved)
    if not verify_target:
        return
    descriptor = win32security.GetFileSecurity(
        str(resolved),
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION,
    )
    owner = win32security.ConvertSidToStringSid(
        descriptor.GetSecurityDescriptorOwner()
    )
    if owner not in trusted_sids:
        raise PermissionError(f"数据目录所有者不受信任：{resolved}")
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        raise PermissionError(f"数据目录存在允许所有人访问的空 ACL：{resolved}")
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        ace_type, ace_flags = ace[0]
        if ace_flags & 0x08:  # INHERIT_ONLY_ACE 不作用于当前目录。
            continue
        if ace_type not in {
            win32security.ACCESS_ALLOWED_ACE_TYPE,
            getattr(win32security, "ACCESS_ALLOWED_OBJECT_ACE_TYPE", 5),
        }:
            continue
        sid = win32security.ConvertSidToStringSid(ace[-1])
        if sid in trusted_sids:
            continue
        if int(ace[1]) & target_write_mask:
            raise PermissionError(
                f"现有数据目录允许非受信主体修改或替换：{resolved}（{sid}）"
            )


def _assert_windows_service_data_root_adoptable(data_dir: Path) -> None:
    """只允许接管受信任管理员创建的目录，随后再收敛其 ACL。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    import win32security  # type: ignore[import-untyped]

    resolved = data_dir.resolve(strict=True)
    _assert_managed_data_tree_has_no_reparse_points(resolved)
    descriptor = win32security.GetFileSecurity(
        str(resolved),
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION,
    )
    owner = win32security.ConvertSidToStringSid(
        descriptor.GetSecurityDescriptorOwner()
    )
    trusted_sids = {
        "S-1-5-18",
        "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    }
    try:
        current_sid, _domain, _kind = win32security.LookupAccountName(
            None, getpass.getuser()
        )
        trusted_sids.add(win32security.ConvertSidToStringSid(current_sid))
    except Exception:  # noqa: BLE001 - 服务账号查名失败时仍保留系统 SID 白名单。
        pass
    if owner not in trusted_sids:
        raise PermissionError(
            f"数据目录不是由当前管理员、SYSTEM 或管理员组创建：{resolved}"
        )
    if descriptor.GetSecurityDescriptorDacl() is None:
        raise PermissionError(f"数据目录存在允许所有人访问的空 ACL：{resolved}")


def _validate_windows_data_dir(data_dir: Path) -> Path:
    """Windows 正式模式：允许用户自定义数据目录，但拒绝系统关键目录。

    数据目录承载业务数据库、备份与证书。Windows 主机服务以 SYSTEM 运行，
    只要不是系统关键目录即可；推荐放在数据盘（如 D:\\PartyOps-数据）。
    """
    if not str(data_dir).strip():
        data_dir = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps-Data"
    expanded = data_dir.expanduser()
    if str(expanded).startswith("\\\\"):
        raise ValueError("主机数据目录不能使用网络共享路径，请选择本机固定磁盘")
    _assert_path_components_have_no_reparse_points(expanded)
    resolved = expanded.resolve()
    if str(resolved).startswith("\\\\"):
        raise ValueError("主机数据目录不能使用网络共享路径，请选择本机固定磁盘")
    root = Path(resolved.anchor)
    if resolved == root:
        raise ValueError(
            "数据目录不能是磁盘根目录，请选择具体文件夹，例如 D:\\PartyOps-数据"
        )
    system_markers = []
    for env_name, fallback in (
        ("WINDIR", r"C:\Windows"),
        ("ProgramFiles", r"C:\Program Files"),
        ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        raw = os.getenv(env_name) or fallback
        try:
            system_markers.append(Path(raw).resolve())
        except OSError:
            continue
    for marker in system_markers:
        if resolved == marker or marker in resolved.parents:
            raise ValueError(
                "数据目录不能放在系统目录中，请选择普通数据盘目录，例如 D:\\PartyOps-数据"
            )
    reserved_roots = []
    for env_name in ("PROGRAMDATA", "USERPROFILE"):
        raw = os.getenv(env_name)
        if raw:
            try:
                reserved_roots.append(Path(raw).resolve())
            except OSError:
                continue
    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        try:
            reserved_roots.append(Path(user_profile).resolve().parent)
        except OSError:
            pass
    if resolved in reserved_roots:
        raise ValueError(
            "数据目录不能直接使用系统共享目录或用户主目录，请在其中选择 PartyOps 专用子目录"
        )
    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        profile = Path(user_profile).resolve()
        profiles_root = profile.parent
        if profile in resolved.parents or profiles_root in resolved.parents:
            raise ValueError(
                "主机数据目录不能放在用户主目录下，请选择独立数据盘目录或系统默认目录"
            )
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(
            f"无法创建数据目录 {resolved}，请检查权限或换一个位置。"
        ) from exc
    _assert_path_components_have_no_reparse_points(resolved)
    _assert_windows_service_data_root_adoptable(resolved)
    try:
        import ctypes

        drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(root))  # type: ignore[attr-defined]
        if drive_type != 3:  # DRIVE_FIXED
            raise ValueError("主机数据目录必须位于本机固定磁盘，不能使用移动盘或网络盘")
    except AttributeError:
        # 非 Windows 单元测试没有 kernel32；路径其余边界仍照常验证。
        pass
    try:
        if shutil.disk_usage(resolved).free < 2 * 1024**3:
            raise ValueError("所选数据目录可用空间不足 2GB，请清理空间或更换目录")
        probe = resolved / f".partyops-write-test-{secrets.token_hex(8)}"
        probe.write_bytes(b"PartyOps")
        probe.unlink()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("所选数据目录不可写，请检查权限或更换目录") from exc
    return resolved


def _validate_personal_data_dir(data_dir: Path) -> Path:
    """核验个人模式目录，但不施加 SYSTEM 服务目录的所有权要求。"""

    if not str(data_dir).strip():
        data_dir = personal_default_data_dir()
    expanded = data_dir.expanduser()
    if str(expanded).startswith("\\\\"):
        raise ValueError("个人数据目录不能使用网络共享路径，请选择本机固定磁盘")
    _assert_path_components_have_no_reparse_points(expanded)
    resolved = expanded.resolve()
    root = Path(resolved.anchor)
    if resolved == root:
        raise ValueError("个人数据目录不能是磁盘根目录，请选择具体文件夹")
    for env_name, fallback in (
        ("WINDIR", r"C:\Windows"),
        ("ProgramFiles", r"C:\Program Files"),
        ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        marker = Path(os.getenv(env_name) or fallback).resolve()
        if resolved == marker or marker in resolved.parents:
            raise ValueError("个人数据目录不能放在系统或程序目录中")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        _assert_path_components_have_no_reparse_points(resolved)
        if os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test":
            import ctypes

            if ctypes.windll.kernel32.GetDriveTypeW(str(root)) != 3:  # type: ignore[attr-defined]
                raise ValueError("个人数据目录必须位于本机固定磁盘")
        if shutil.disk_usage(resolved).free < 2 * 1024**3:
            raise ValueError("所选个人数据目录可用空间不足 2GB")
        probe = resolved / f".partyops-write-test-{secrets.token_hex(8)}"
        probe.write_bytes(b"PartyOps")
        probe.unlink()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("所选个人数据目录不可写，请检查权限或更换目录") from exc
    return resolved


def _grant_windows_service_access(data_dir: Path) -> None:
    """把主机业务数据收敛为仅 SYSTEM 与管理员可修改。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    _assert_managed_data_tree_has_no_reparse_points(data_dir)
    _assert_windows_service_data_root_adoptable(data_dir)
    has_children = any(data_dir.iterdir())
    commands = [
        [
            "icacls.exe",
            str(data_dir),
            "/setowner",
            "*S-1-5-32-544",
            "/Q",
        ],
        [
            "icacls.exe",
            str(data_dir),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "/Q",
        ],
    ]
    if has_children:
        # 根目录先锁定后再处理子项，避免把 (OI)(CI) 继承标记递归写到普通
        # 文件并产生无有效 ACE 的载荷；/L 确保即使发生竞态也只处理链接本身。
        commands.extend(
            [
                [
                    "icacls.exe",
                    str(data_dir / "*"),
                    "/setowner",
                    "*S-1-5-32-544",
                    "/T",
                    "/L",
                    "/Q",
                ],
                [
                    "icacls.exe",
                    str(data_dir / "*"),
                    "/reset",
                    "/T",
                    "/L",
                    "/Q",
                ],
            ]
        )
    for command in commands:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise ValueError("无法保护所选主机数据目录权限，请更换目录后重试")
    _assert_managed_data_tree_has_no_reparse_points(data_dir)
    assert_windows_service_data_path_security(data_dir, verify_target=True)


def normalize_windows_service_data_path_security(data_dir: Path) -> None:
    """升级旧版自定义数据目录权限，供向导与监督服务共用。"""

    _grant_windows_service_access(data_dir)


def _protect_windows_control_config(config_path: Path) -> None:
    """控制配置只读开放给普通用户，业务数据不随之开放。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    mode_path = config_path.parent / "mode.json"
    _assert_path_components_have_no_reparse_points(config_path.parent)
    commands = [
        [
            "icacls.exe",
            str(config_path.parent),
            "/setowner",
            "*S-1-5-32-544",
            "/T",
            "/C",
            "/Q",
        ],
        [
            "icacls.exe",
            str(config_path.parent),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
            "*S-1-5-32-545:(RX)",
            "/Q",
        ],
    ]
    for readable in (config_path, mode_path):
        commands.append(
            [
                "icacls.exe",
                str(readable),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:F",
                "*S-1-5-32-544:F",
                "*S-1-5-32-545:R",
                "/Q",
            ]
        )
    for command in commands:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise ValueError("无法保护 PartyOps 系统配置权限，主机配置未完成")


def _windows_service_running(service: str) -> bool:
    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return False
    result = subprocess.run(
        ["sc.exe", "query", service],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0 and bool(
        re.search(r"STATE\s*:\s*4\b", result.stdout + result.stderr, re.IGNORECASE)
    )


def _windows_service_start_config(service: str) -> tuple[int, bool] | None:
    """读取服务启动类型；返回 ``(Start, DelayedAutoStart)``。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{service}",
            0,
            winreg.KEY_READ,
        ) as key:
            start = int(winreg.QueryValueEx(key, "Start")[0])
            try:
                delayed = bool(int(winreg.QueryValueEx(key, "DelayedAutoStart")[0]))
            except FileNotFoundError:
                delayed = False
    except FileNotFoundError:
        return None
    return start, delayed


def _restore_windows_service_start_config(
    service: str, config: tuple[int, bool] | None
) -> None:
    """精确恢复服务启动类型，失败时拒绝假装模式切换已回滚。"""

    if config is None or os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    start, delayed = config
    start_name = {
        2: "delayed-auto" if delayed else "auto",
        3: "demand",
        4: "disabled",
    }.get(start)
    if start_name is None:
        raise ValueError(f"无法恢复 {service} 的未知启动类型 {start}")
    result = subprocess.run(
        ["sc.exe", "config", service, "start=", start_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise ValueError(f"无法恢复 {service} 的原启动类型")
    restored = _windows_service_start_config(service)
    if restored != config:
        raise ValueError(f"{service} 启动类型回读不一致")


def _stop_windows_service_for_data_migration(timeout: float = 45.0) -> dict[str, bool]:
    """迁移前协调停止更新与主机服务，并返回精确的原运行状态。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return {}
    services = ("PartyOpsUpdateService", "PartyOpsHost")
    states = {service: _windows_service_running(service) for service in services}
    for service in services:
        subprocess.run(
            ["sc.exe", "stop", service],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    for service in services:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["sc.exe", "query", service],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output = (result.stdout + result.stderr).lower()
            if (
                result.returncode == 1060
                or "does not exist" in output
                or "未安装" in output
            ):
                break
            if re.search(
                r"STATE\s*:\s*1\b", result.stdout + result.stderr, re.IGNORECASE
            ):
                break
            time.sleep(1.0)
        else:
            _restore_windows_services_after_data_migration(states)
            raise ValueError(
                f"迁移数据前无法停止 {service}，请等待正在进行的升级结束后重试"
            )
    return states


def _restore_windows_services_after_data_migration(states: dict[str, bool]) -> None:
    """迁移失败时只恢复原本运行的服务，不改变用户主动停止的服务。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    for service in ("PartyOpsUpdateService", "PartyOpsHost"):
        if not states.get(service):
            continue
        subprocess.run(
            ["sc.exe", "start", service],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def _windows_privileged_update_lock_path() -> Path:
    """与 SYSTEM 更新器共用同一个受保护事务锁，不能在业务目录另造哨兵。"""

    return (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps-System"
        / "update.lock"
    )


def _acquire_windows_data_migration_lock() -> Path:
    """在停服务前原子占有 SYSTEM 更新锁，关闭先检查后停服的竞态。"""

    from .update_executor import _acquire_update_lock

    lock_path = _windows_privileged_update_lock_path()
    if not _acquire_update_lock(lock_path):
        raise ValueError("检测到正在进行或待恢复的 PartyOps 更新事务，数据目录尚未迁移")
    return lock_path


def _release_windows_data_migration_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(
            "数据迁移已结束，但更新事务锁未能安全释放；请不要启动更新并联系管理员"
        ) from exc


def _windows_system_host_role_active() -> bool:
    """仅在系统控制配置仍声明 host 时要求 UAC 停用旧主机。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return False
    root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
    mode_path = root / "mode.json"
    if mode_path.is_file():
        try:
            return (
                json.loads(mode_path.read_text(encoding="utf-8")).get("mode") == "host"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return True
    return (root / "partyops.env").is_file()


def _windows_host_switch_snapshot_path() -> Path:
    """返回只允许管理员和 SYSTEM 写入的模式切换回滚记录。"""

    return (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps-System"
        / "host-switch-rollback.json"
    )


def _windows_host_switch_pending_path() -> Path:
    """普通用户只读取此非敏感标记，以发现需要管理员恢复的中断事务。"""

    return (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        / "PartyOps"
        / "host-switch-pending.json"
    )


def _write_windows_host_switch_snapshot(
    *,
    transaction_id: str = "",
    previous_mode: str | None,
    start_configs: dict[str, tuple[int, bool] | None],
    running_states: dict[str, bool],
) -> None:
    """在停用主机前持久化最小、可验证的特权回滚状态。"""

    payload = {
        "format_version": 1,
        "transaction_id": transaction_id,
        "previous_mode": previous_mode,
        "services": {
            service: {
                "start_type": config[0] if config is not None else None,
                "delayed": config[1] if config is not None else False,
                "running": bool(running_states.get(service)),
            }
            for service, config in start_configs.items()
        },
    }
    snapshot_path = _windows_host_switch_snapshot_path()
    _write_private(
        snapshot_path,
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    try:
        _write_private(
            _windows_host_switch_pending_path(),
            json.dumps(
                {"format_version": 1, "transaction_id": transaction_id},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            0o644,
        )
    except Exception:
        snapshot_path.unlink(missing_ok=True)
        raise


def _validated_windows_host_switch_snapshot(
    expected_transaction_id: str = "",
) -> tuple[Path, str | None, dict[str, dict[str, object]]]:
    """先完整校验受保护快照，再允许任何恢复或提交副作用。"""

    snapshot_path = _windows_host_switch_snapshot_path()
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "[MODE_SWITCH_SNAPSHOT_INVALID] 主机模式回滚记录不可用"
        ) from exc
    if payload.get("format_version") != 1 or not isinstance(
        payload.get("services"), dict
    ):
        raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 主机模式回滚记录格式无效")
    allowed_services = {"PartyOpsHost", "PartyOpsUpdateService"}
    services = payload["services"]
    if set(services) != allowed_services:
        raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 主机模式回滚服务清单无效")
    transaction_id = payload.get("transaction_id", "")
    if not isinstance(transaction_id, str) or (
        transaction_id and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", transaction_id)
    ):
        raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 模式切换事务编号无效")
    if expected_transaction_id and transaction_id != expected_transaction_id:
        raise ValueError(
            "[MODE_SWITCH_SNAPSHOT_MISMATCH] 模式切换事务已变化，拒绝误操作"
        )
    previous_mode = payload.get("previous_mode")
    if previous_mode is not None and not isinstance(previous_mode, str):
        raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 主机模式回滚内容无效")
    if previous_mode is not None:
        try:
            parsed_mode = json.loads(previous_mode)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "[MODE_SWITCH_SNAPSHOT_INVALID] 回滚记录不是有效主机模式"
            ) from exc
        if not isinstance(parsed_mode, dict) or parsed_mode.get("mode") != "host":
            raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 回滚记录不是主机模式")
    for service in ("PartyOpsHost", "PartyOpsUpdateService"):
        state = services[service]
        if not isinstance(state, dict):
            raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 服务回滚状态无效")
        raw_start = state.get("start_type")
        try:
            config = (
                None
                if raw_start is None
                else (int(raw_start), bool(state.get("delayed")))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 服务启动类型无效") from exc
        if config is not None and config[0] not in {2, 3, 4}:
            raise ValueError("[MODE_SWITCH_SNAPSHOT_INVALID] 服务启动类型无效")
    return snapshot_path, previous_mode, services


def _restore_windows_host_switch_privileged(expected_transaction_id: str = "") -> None:
    """在用户角色写入失败或上次断电后恢复主机服务与模式。"""

    if os.name != "nt" or not windows_is_admin():
        raise ValueError("恢复 Windows 主机角色需要管理员权限")
    snapshot_path, previous_mode, services = _validated_windows_host_switch_snapshot(
        expected_transaction_id
    )
    mode_path = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "mode.json"
    )
    if previous_mode is None:
        mode_path.unlink(missing_ok=True)
    else:
        _write_private(mode_path, previous_mode)
    for service in ("PartyOpsHost", "PartyOpsUpdateService"):
        state = services[service]
        raw_start = state.get("start_type")
        config = (
            None if raw_start is None else (int(raw_start), bool(state.get("delayed")))
        )
        _restore_windows_service_start_config(service, config)
    _restore_windows_services_after_data_migration(
        {
            service: bool(services[service].get("running"))
            for service in ("PartyOpsHost", "PartyOpsUpdateService")
        }
    )
    _windows_host_switch_pending_path().unlink(missing_ok=True)
    snapshot_path.unlink(missing_ok=True)


def _finalize_windows_host_switch_privileged(transaction_id: str) -> None:
    """目标角色配置全部落盘后，提交并清除受保护的回滚记录。"""

    if os.name != "nt" or not windows_is_admin():
        raise ValueError("提交 Windows 模式切换需要管理员权限")
    snapshot_path, _previous_mode, _services = _validated_windows_host_switch_snapshot(
        transaction_id
    )
    _windows_host_switch_pending_path().unlink(missing_ok=True)
    snapshot_path.unlink(missing_ok=False)


def _run_windows_host_switch_helper(argument: str, transaction_id: str = "") -> None:
    """通过固定向导执行一次受控的模式切换特权动作。"""

    if windows_is_admin():
        if argument == "--privileged-disable-host":
            if transaction_id:
                _deactivate_windows_host_services_privileged(transaction_id)
            else:
                _deactivate_windows_host_services_privileged()
        elif argument == "--privileged-restore-host":
            if transaction_id:
                _restore_windows_host_switch_privileged(transaction_id)
            else:
                _restore_windows_host_switch_privileged()
        elif argument == "--privileged-finalize-host-switch":
            _finalize_windows_host_switch_privileged(transaction_id)
        else:
            raise ValueError("模式切换管理员动作无效")
        return
    wizard = _executable("PartyOpsWizard")
    script = (
        "$ErrorActionPreference='Stop'; try { "
        "$a=@($args[1]); if($args[2]){$a += '--mode-switch-transaction'; $a += $args[2]}; "
        "$p=Start-Process -FilePath $args[0] -ArgumentList $a "
        "-Verb RunAs -Wait -PassThru; "
        "if($null -eq $p){throw '管理员进程未创建'}; exit $p.ExitCode "
        "} catch { $native=$_.Exception.NativeErrorCode; "
        "if(-not $native){$native=$_.Exception.HResult -band 0xffff}; "
        "[Console]::Error.WriteLine($_.Exception.Message); exit $native }"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            str(wizard),
            argument,
            transaction_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode == 0:
        return
    detail = (result.stdout + "\n" + result.stderr).strip()[-2000:]
    if _windows_policy_blocked(detail, result.returncode):
        raise HostStartupError(
            ADMIN_POLICY_BLOCKED,
            "单位策略阻止 PartyOps 完成模式切换。程序不会绕过安全策略；"
            "请联系管理员允许 PartyOpsWizard.exe 后重试。",
            detail=detail,
        )
    raise ValueError("Windows 管理员确认未完成，系统已保留原模式。")


def _deactivate_windows_host_services_privileged(transaction_id: str = "") -> None:
    """切到个人/协同时停止 LAN 主机并撤销自启动与防火墙。"""

    if os.name != "nt" or not windows_is_admin():
        raise ValueError("停用 Windows 主机角色需要管理员权限")
    if not transaction_id:
        transaction_id = secrets.token_urlsafe(32)
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", transaction_id):
        raise ValueError("模式切换事务编号无效")
    start_configs = {
        service: _windows_service_start_config(service)
        for service in ("PartyOpsHost", "PartyOpsUpdateService")
    }
    mode_path = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "mode.json"
    )
    previous_mode = (
        mode_path.read_text(encoding="utf-8") if mode_path.is_file() else None
    )
    states = {
        service: _windows_service_running(service)
        for service in ("PartyOpsHost", "PartyOpsUpdateService")
    }
    _write_windows_host_switch_snapshot(
        transaction_id=transaction_id,
        previous_mode=previous_mode,
        start_configs=start_configs,
        running_states=states,
    )
    try:
        _stop_windows_service_for_data_migration()
        for service in ("PartyOpsHost", "PartyOpsUpdateService"):
            result = subprocess.run(
                ["sc.exe", "config", service, "start=", "demand"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode not in {0, 1060}:
                raise ValueError(f"无法停用 {service} 自动启动")
        _write_private(
            mode_path,
            json.dumps(
                {"format_version": 1, "mode": "personal"}, ensure_ascii=False, indent=2
            ),
        )
        firewall_result = subprocess.run(
            [
                "netsh.exe",
                "advfirewall",
                "firewall",
                "delete",
                "rule",
                "name=党建智办主机",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if firewall_result.returncode != 0:
            raise ValueError("无法撤销旧主机的局域网防火墙规则")
    except Exception as original_error:
        rollback_errors: list[str] = []
        try:
            if previous_mode is None:
                mode_path.unlink(missing_ok=True)
            else:
                _write_private(mode_path, previous_mode)
        except Exception as exc:  # noqa: BLE001 - 汇总回滚诊断，不能掩盖任何一步。
            rollback_errors.append(f"模式配置：{exc}")
        for service, config in start_configs.items():
            try:
                _restore_windows_service_start_config(service, config)
            except Exception as exc:  # noqa: BLE001 - 汇总回滚诊断。
                rollback_errors.append(f"{service}：{exc}")
        _restore_windows_services_after_data_migration(states)
        if rollback_errors:
            raise ValueError(
                "[MODE_SWITCH_ROLLBACK_FAILED] 模式切换失败且旧服务状态未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        _windows_host_switch_pending_path().unlink(missing_ok=True)
        _windows_host_switch_snapshot_path().unlink(missing_ok=True)
        raise


def recover_pending_windows_host_switch() -> None:
    """向导重新打开时先恢复未提交事务，不能把断电后的半切换当作成功。"""

    if (
        os.name != "nt"
        or os.getenv("PARTYOPS_ENVIRONMENT") == "test"
        or not _windows_host_switch_pending_path().exists()
    ):
        return
    _run_windows_host_switch_helper("--privileged-restore-host")


def deactivate_windows_host_for_user_mode() -> str | None:
    """只有从已启用主机切出时申请一次 UAC；全新个人模式不提权。"""

    recover_pending_windows_host_switch()
    if not _windows_system_host_role_active():
        return None
    transaction_id = secrets.token_urlsafe(32)
    _run_windows_host_switch_helper("--privileged-disable-host", transaction_id)
    return transaction_id


def finalize_windows_host_switch(transaction_id: str | bool | None) -> None:
    """成功写完用户角色后，以同一事务编号提交特权切换。"""

    if not isinstance(transaction_id, str) or not transaction_id:
        return
    _run_windows_host_switch_helper("--privileged-finalize-host-switch", transaction_id)


def restore_windows_host_after_failed_switch(
    deactivated: str | bool | None,
) -> None:
    """用户角色提交失败时恢复旧主机；恢复失败必须给出稳定诊断。"""

    if not deactivated:
        return
    try:
        transaction_id = deactivated if isinstance(deactivated, str) else ""
        _run_windows_host_switch_helper("--privileged-restore-host", transaction_id)
    except Exception as exc:
        raise ValueError(
            "[MODE_SWITCH_ROLLBACK_FAILED] 新角色配置失败，且旧主机未能完整恢复；"
            "请打开日志目录并重新运行配置向导。"
        ) from exc


def _verify_sqlite_copy(database: Path) -> None:
    """迁移切换前只读校验 SQLite，避免复制到一半的数据成为新主库。"""

    if not database.is_file():
        return
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if not result or result[0] != "ok":
        raise ValueError("迁移后的数据库完整性检查未通过，已保留原数据目录")


def migrate_windows_data_dir(source: Path, target: Path) -> None:
    """复制并校验旧业务数据；成功前不修改配置，也不删除原目录。"""

    _assert_path_components_have_no_reparse_points(source)
    _assert_path_components_have_no_reparse_points(target)
    source = source.resolve()
    target = target.resolve()
    if source == target or not source.exists():
        return
    if source in target.parents or target in source.parents:
        raise ValueError("新旧数据目录不能互相嵌套，请选择独立文件夹")
    business_names = {
        "partyops.db",
        "attachments",
        "backups",
        "exports",
        "archives",
        "logs",
        "updates",
        "transfers",
        "inbox",
        "secrets",
        "models",
        "cache",
        "upgrade-backups",
        "installer-cache",
        "received-files",
        "接收文件",
        "launcher.log",
    }
    existing = [
        path
        for path in target.iterdir()
        if path.name not in {"partyops.env", "mode.json", ".partyops-data-root.json"}
    ]
    if existing:
        raise ValueError("新数据目录不是空目录。为防止混合两套业务数据，请选择空文件夹")
    if not any((source / name).exists() for name in business_names):
        return
    _assert_managed_data_tree_has_no_reparse_points(source)
    staging = target.with_name(
        f".{target.name}.partyops-migrating-{secrets.token_hex(6)}"
    )
    staging.mkdir(parents=False, exist_ok=False)
    control_names = {"partyops.env", "mode.json", ".partyops-data-root.json"}
    target_controls: dict[str, bytes] = {}
    try:
        for name in business_names:
            origin = source / name
            destination = staging / name
            if origin.is_dir():
                shutil.copytree(
                    origin,
                    destination,
                    symlinks=True,
                    copy_function=shutil.copy2,
                )
            elif origin.is_file() and name != "partyops.db":
                shutil.copy2(origin, destination)
        source_database = source / "partyops.db"
        if source_database.is_file():
            source_connection = sqlite3.connect(
                f"file:{source_database.as_posix()}?mode=ro", uri=True
            )
            destination_connection = sqlite3.connect(staging / "partyops.db")
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
                source_connection.close()
        _assert_managed_data_tree_has_no_reparse_points(staging)
        _verify_sqlite_copy(staging / "partyops.db")
        for name in control_names:
            control = target / name
            if not control.exists():
                continue
            if not control.is_file() or control.is_symlink():
                raise ValueError("新数据目录控制文件不是本机普通文件，请选择空文件夹")
            target_controls[name] = control.read_bytes()
            shutil.copy2(control, staging / name)
        for name in target_controls:
            (target / name).unlink()
        target.rmdir()
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        for name, content in target_controls.items():
            control = target / name
            if not control.exists():
                control.write_bytes(content)
        raise


def _personal_process_marker(data_dir: Path) -> Path:
    return data_dir / ".partyops-personal-process.json"


def _process_executable_matches(pid: int, expected: Path) -> bool:
    """核对 PID 仍指向随包主程序，防止 PID 复用后误终止其他进程。"""

    if pid <= 0:
        return False
    expected_text = os.path.normcase(str(expected.resolve()))
    if os.name != "nt":
        try:
            return (
                os.path.normcase(str(Path(f"/proc/{pid}/exe").resolve()))
                == expected_text
            )
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return False
        return os.path.normcase(str(Path(buffer.value).resolve())) == expected_text
    finally:
        kernel32.CloseHandle(handle)


def _stop_personal_process_for_data_migration(data_dir: Path, port: int) -> bool:
    """停止由当前安装记录的个人进程；身份不明时拒绝迁移。"""

    marker = _personal_process_marker(data_dir)
    if not marker.is_file() or marker.is_symlink():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                raise ValueError(
                    "旧个人模式仍在运行但缺少受控进程标记。请先在托盘退出 PartyOps，"
                    "再重新选择数据目录。"
                )
        except (ConnectionRefusedError, TimeoutError, OSError):
            return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0))
        recorded = Path(str(payload.get("executable", ""))).resolve()
        expected = _executable("partyops").resolve()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("个人模式进程标记损坏，请先退出 PartyOps 后重试") from None
    if recorded != expected or not _process_executable_matches(pid, expected):
        marker.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        marker.unlink(missing_ok=True)
        return False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and _process_executable_matches(pid, expected):
        time.sleep(0.25)
    if _process_executable_matches(pid, expected):
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    if _process_executable_matches(pid, expected):
        raise ValueError("旧个人模式进程未能安全停止，数据目录尚未切换")
    marker.unlink(missing_ok=True)
    return True


def _record_personal_process(data_dir: Path, process: subprocess.Popen | None) -> None:
    if process is None or not getattr(process, "pid", 0):
        return
    _write_private(
        _personal_process_marker(data_dir),
        json.dumps(
            {
                "format_version": 1,
                "pid": process.pid,
                "executable": str(_executable("partyops").resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def _restart_previous_personal_process(environment: dict[str, str]) -> None:
    data_dir = Path(environment["PARTYOPS_DATA_DIR"])
    process = _spawn(
        [str(_executable("partyops"))], data_dir / "launcher.log", environment
    )
    _record_personal_process(data_dir, process)


def write_host_config(
    host: str,
    port: int,
    data_dir: Path,
    *,
    write_user_mode: bool = True,
) -> Path:
    validate_host_config_selection(host, port)
    windows_system_mode = (
        os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
    )
    if windows_system_mode:
        # Windows 正式安装：允许用户自定义数据目录（默认 ProgramData\PartyOps-Data），
        # 不再强制固定到系统盘。
        resolved_data_dir = _validate_windows_data_dir(data_dir)
    else:
        resolved_data_dir = data_dir.expanduser().resolve()
    path = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "partyops.env"
        if windows_system_mode
        else config_root() / "partyops.env"
    )
    system_mode_path = path.parent / "mode.json"
    user_mode_path = config_root() / "mode.json"
    marker_path = resolved_data_dir / ".partyops-data-root.json"
    transaction_paths = {path, marker_path}
    if write_user_mode:
        transaction_paths.add(user_mode_path)
    if windows_system_mode:
        transaction_paths.add(system_mode_path)
    previous_files = {
        candidate: candidate.read_text(encoding="utf-8")
        if candidate.is_file()
        else None
        for candidate in transaction_paths
    }
    previous = load_host_environment(path) if path.is_file() else {}
    migration_service_states: dict[str, bool] = {}
    migration_lock: Path | None = None
    if windows_system_mode:
        if previous:
            previous_raw = previous.get("PARTYOPS_DATA_DIR", "").strip()
            if previous_raw:
                previous_data_dir = Path(previous_raw).expanduser().resolve()
                if previous_data_dir != resolved_data_dir:
                    migration_lock = _acquire_windows_data_migration_lock()
                    try:
                        migration_service_states = (
                            _stop_windows_service_for_data_migration() or {}
                        )
                        migrate_windows_data_dir(previous_data_dir, resolved_data_dir)
                    except Exception:
                        _restore_windows_services_after_data_migration(
                            migration_service_states
                        )
                        raise
                    finally:
                        _release_windows_data_migration_lock(migration_lock)
                        migration_lock = None
        try:
            _grant_windows_service_access(resolved_data_dir)
        except Exception:
            _restore_windows_services_after_data_migration(migration_service_states)
            raise
    values = {
        "PARTYOPS_MODE": "host",
        "PARTYOPS_ENVIRONMENT": "production",
        "PARTYOPS_HOST": host,
        "PARTYOPS_BIND_HOST": "127.0.0.1" if host == "127.0.0.1" else "0.0.0.0",  # nosec B104 - 局域网主机模式需要通配监听，并由明确展示地址与防火墙共同约束。
        "PARTYOPS_ADVERTISE_HOST": host,
        "PARTYOPS_PORT": str(port),
        "PARTYOPS_AGENT_PORT": str(port + 1),
        "PARTYOPS_DATA_DIR": str(resolved_data_dir),
        "PARTYOPS_STRICT_SQLITE": "true",
        "PARTYOPS_SEED_DEMO": "false",
        "PARTYOPS_TLS_ENABLED": "true",
        "PARTYOPS_BOOTSTRAP_TOKEN": (
            previous.get("PARTYOPS_BOOTSTRAP_TOKEN", "").strip()
            or secrets.token_urlsafe(32)
        ),
    }
    public_key_candidates = (
        runtime_root() / "update-public-key.txt",
        runtime_root() / "packaging" / "uos" / "update-public-key.txt",
    )
    public_key_path = next(
        (path for path in public_key_candidates if path.is_file()),
        None,
    )
    if public_key_path:
        values["PARTYOPS_UPDATE_PUBLIC_KEY"] = public_key_path.read_text(
            encoding="utf-8"
        ).strip()
    content = (
        "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n"
    )
    try:
        _write_private(path, content)
        _write_data_root_marker(resolved_data_dir, "host")
        if write_user_mode:
            write_mode_config("host", config_path=path)
        if windows_system_mode:
            _write_private(
                system_mode_path,
                json.dumps(
                    {"format_version": 1, "mode": "host", "config_path": str(path)},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            _protect_windows_control_config(path)
    except Exception as original_error:
        rollback_errors: list[str] = []
        for candidate, previous_content in previous_files.items():
            try:
                if previous_content is None:
                    candidate.unlink(missing_ok=True)
                else:
                    _write_private(candidate, previous_content)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"{candidate.name}：{exc}")
        _restore_windows_services_after_data_migration(migration_service_states)
        if rollback_errors:
            raise ValueError(
                "[HOST_CONFIG_ROLLBACK_FAILED] 主机配置失败且旧控制配置未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        raise
    _restore_windows_services_after_data_migration(migration_service_states)
    return path


def write_personal_config(data_dir: Path, port: int = 18775) -> Path:
    """创建仅当前账号可用的回环配置，全程不申请管理员权限。"""

    if not 1024 <= port <= 65534:
        raise ValueError("个人模式端口必须在 1024—65534 之间")
    resolved_data_dir = (
        _validate_personal_data_dir(data_dir)
        if os.name == "nt"
        else data_dir.expanduser().resolve()
    )
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    path = config_root() / "personal.env"
    mode_path = config_root() / "mode.json"
    marker_path = resolved_data_dir / ".partyops-data-root.json"
    linux_autostart_path = config_root().parent / "autostart" / "partyops-host.desktop"
    transaction_paths = (path, mode_path, marker_path) + (
        (linux_autostart_path,) if sys.platform.startswith("linux") else ()
    )
    previous_files = {
        candidate: candidate.read_text(encoding="utf-8")
        if candidate.is_file()
        else None
        for candidate in transaction_paths
    }
    try:
        previous_mode = (
            json.loads(previous_files[mode_path]).get("mode")
            if previous_files[mode_path]
            else ""
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        previous_mode = ""
    previous = load_host_environment(path) if path.is_file() else {}
    previous_process_stopped = False
    host_deactivated: str | bool | None = None
    previous_data_dir: Path | None = None
    previous_raw = previous.get("PARTYOPS_DATA_DIR", "").strip()
    try:
        host_deactivated = deactivate_windows_host_for_user_mode()
        if previous_raw:
            previous_data_dir = Path(previous_raw).expanduser().resolve()
            if previous_data_dir != resolved_data_dir:
                previous_port = int(previous.get("PARTYOPS_PORT", "18775"))
                previous_process_stopped = _stop_personal_process_for_data_migration(
                    previous_data_dir, previous_port
                )
                migrate_windows_data_dir(previous_data_dir, resolved_data_dir)
        values = {
            "PARTYOPS_MODE": "personal",
            "PARTYOPS_ENVIRONMENT": "production",
            "PARTYOPS_HOST": "127.0.0.1",
            "PARTYOPS_BIND_HOST": "127.0.0.1",
            "PARTYOPS_ADVERTISE_HOST": "127.0.0.1",
            "PARTYOPS_PORT": str(port),
            "PARTYOPS_AGENT_PORT": str(port + 1),
            "PARTYOPS_DATA_DIR": str(resolved_data_dir),
            "PARTYOPS_STRICT_SQLITE": "true",
            "PARTYOPS_SEED_DEMO": "false",
            "PARTYOPS_TLS_ENABLED": "false",
            "PARTYOPS_BOOTSTRAP_TOKEN": (
                previous.get("PARTYOPS_BOOTSTRAP_TOKEN", "").strip()
                or secrets.token_urlsafe(32)
            ),
        }
        public_key_candidates = (
            runtime_root() / "update-public-key.txt",
            runtime_root() / "packaging" / "uos" / "update-public-key.txt",
        )
        public_key_path = next(
            (candidate for candidate in public_key_candidates if candidate.is_file()),
            None,
        )
        if public_key_path:
            values["PARTYOPS_UPDATE_PUBLIC_KEY"] = public_key_path.read_text(
                encoding="utf-8"
            ).strip()
        content = (
            "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
            + "\n"
        )
        _write_private(path, content)
        _write_data_root_marker(resolved_data_dir, "personal")
        write_mode_config("personal", config_path=path)
        clear_windows_client_autostart()
        if sys.platform.startswith("linux"):
            install_host_autostart(path)
        else:
            install_windows_personal_autostart()
        finalize_windows_host_switch(host_deactivated)
    except Exception as original_error:
        rollback_errors: list[str] = []
        for candidate, previous_content in previous_files.items():
            try:
                if previous_content is None:
                    candidate.unlink(missing_ok=True)
                else:
                    _write_private(candidate, previous_content)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"{candidate.name}：{exc}")
        if previous_process_stopped and previous_data_dir is not None:
            try:
                _restart_previous_personal_process(previous)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"个人进程：{exc}")
        try:
            # 提交失败时先撤销本次可能写入的个人自启动，再精确恢复旧角色。
            clear_windows_personal_autostart()
            if previous_mode == "personal":
                install_windows_personal_autostart()
            elif (
                previous_mode == "client" and (config_root() / "client.json").is_file()
            ):
                install_client_autostart(config_root() / "client.json")
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"自启动：{exc}")
        try:
            restore_windows_host_after_failed_switch(host_deactivated)
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"主机模式：{exc}")
        if rollback_errors:
            raise ValueError(
                "[MODE_SWITCH_ROLLBACK_FAILED] 个人模式配置失败且原模式未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        raise
    return path


def windows_is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def clear_windows_client_autostart() -> None:
    """清除当前桌面账号的协同 Agent 自启动，避免主机角色误起 Agent。"""

    if os.name != "nt":
        return
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, "PartyOpsAgent")
    except FileNotFoundError:
        return


def install_windows_personal_autostart() -> None:
    """个人模式随当前账号登录启动，但后台恢复时不擅自打开浏览器。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    import winreg

    command = f'"{_executable("PartyOpsLauncher")}" --background'
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "PartyOpsPersonal", 0, winreg.REG_SZ, command)


def clear_windows_personal_autostart() -> None:
    if os.name != "nt":
        return
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, "PartyOpsPersonal")
    except FileNotFoundError:
        return


def _enable_windows_host_service_autostart() -> None:
    """仅在用户确认主机角色后把主机与更新服务切为随系统启动。"""

    if os.name != "nt" or os.getenv("PARTYOPS_ENVIRONMENT") == "test":
        return
    for service, label in (
        ("PartyOpsHost", "主机服务"),
        ("PartyOpsUpdateService", "更新服务"),
    ):
        result = subprocess.run(
            ["sc.exe", "config", service, "start=", "auto"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()[-2000:]
            raise HostStartupError(
                SERVICE_MISSING,
                f"无法把 PartyOps {label}设为随 Windows 启动，请重新运行安装器完成修复安装。",
                detail=detail,
            )


def configure_host_config(host: str, port: int, data_dir: Path) -> Path:
    """Windows 仅为主机角色申请一次 UAC，并让日常账号保留正确 mode.json。"""

    validate_host_config_selection(host, port)
    windows_system_mode = (
        os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
    )
    resolved_data_dir = (
        _validate_windows_data_dir(data_dir)
        if windows_system_mode
        else data_dir.expanduser().resolve()
    )
    personal_environment: dict[str, str] = {}
    personal_stopped = False
    host_configured = False
    personal_config = config_root() / "personal.env"
    if windows_system_mode and personal_config.is_file():
        personal_environment = load_host_environment(personal_config)
        personal_data_raw = personal_environment.get("PARTYOPS_DATA_DIR", "").strip()
        if personal_data_raw:
            personal_data = Path(personal_data_raw)
            personal_stopped = _stop_personal_process_for_data_migration(
                personal_data,
                int(personal_environment.get("PARTYOPS_PORT", "18775")),
            )
    try:
        if not windows_system_mode or windows_is_admin():
            path = write_host_config(host, port, resolved_data_dir)
            host_configured = windows_system_mode
            if windows_system_mode:
                _enable_windows_host_service_autostart()
                clear_windows_client_autostart()
                clear_windows_personal_autostart()
            return path

        wizard = _executable("PartyOpsWizard")
        encoded_data_dir = base64.b64encode(
            str(resolved_data_dir).encode("utf-8")
        ).decode("ascii")
        script = (
            "$ErrorActionPreference='Stop'; try { "
            "$data = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($args[3])); "
            "$quotedData = '\"' + $data.Replace('\"', '\\\"') + '\"'; "
            "$process = Start-Process -FilePath $args[0] "
            "-ArgumentList '--privileged-host-config','--host',$args[1],'--port',$args[2],"
            "'--data-dir',$quotedData -Verb RunAs -Wait -PassThru; "
            "if ($null -eq $process) { throw '管理员进程未创建' }; exit $process.ExitCode "
            "} catch { $native=$_.Exception.NativeErrorCode; "
            "if (-not $native) { $native=$_.Exception.HResult -band 0xffff }; "
            "[Console]::Error.WriteLine($_.Exception.Message); exit $native }"
        )
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
                str(wizard),
                host,
                str(port),
                encoded_data_dir,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            detail = (result.stdout + "\n" + result.stderr).strip()[-2000:]
            if _windows_policy_blocked(detail, result.returncode):
                raise HostStartupError(
                    ADMIN_POLICY_BLOCKED,
                    (
                        "Windows 组织策略阻止了 PartyOps 主机配置助手运行。"
                        "只在本机使用时请返回选择“个人使用”，无需管理员授权；"
                        "需要局域网协同时，请让单位电脑管理员允许安装目录中的 "
                        "PartyOpsWizard.exe、PartyOpsService.exe 和 PartyOpsUpdater.exe，"
                        "然后在原数据目录上重试。PartyOps 不会尝试绕过单位安全策略。"
                    ),
                    detail=detail,
                )
            raise ValueError(
                "主机配置需要一次 Windows 管理员授权；授权未完成，系统尚未切换为主机。"
                "如只给自己使用，请返回选择“个人使用”，日常运行不需要管理员权限。"
            )
        path = (
            Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
            / "PartyOps"
            / "partyops.env"
        )
        if not path.is_file():
            raise ValueError("Windows 已返回授权结果，但主机配置文件未生成，请重试")
        host_configured = True
        write_mode_config("host", config_path=path)
        clear_windows_client_autostart()
        clear_windows_personal_autostart()
        return path
    except Exception as original_error:
        rollback_errors: list[str] = []
        if host_configured:
            try:
                deactivate_windows_host_for_user_mode()
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"主机服务：{exc}")
        if personal_stopped and personal_environment:
            try:
                _restart_previous_personal_process(personal_environment)
                install_windows_personal_autostart()
                write_mode_config("personal", config_path=personal_config)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"个人模式：{exc}")
        if rollback_errors:
            raise ValueError(
                "[MODE_SWITCH_ROLLBACK_FAILED] 主机配置失败且原个人模式未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        raise


def write_client_config(
    host_url: str, token: str, backup_dir: Path, interval_seconds: int
) -> Path:
    if not 60 <= interval_seconds <= 86400:
        raise ValueError("灾备拉取间隔必须在 60 秒到 24 小时之间")
    resolved_backup_dir = (
        _validate_windows_data_dir(backup_dir)
        if os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
        else backup_dir.expanduser().resolve()
    )
    config: dict[str, object] = {
        "mode": "client",
        "host_url": host_url.rstrip("/"),
        "pairing_token": token.strip(),
        "backup_dir": str(resolved_backup_dir),
        "receive_dir": str((resolved_backup_dir / "接收文件").resolve()),
        "updates_dir": str((resolved_backup_dir / "updates").resolve()),
        "open_browser": True,
        "interval_seconds": interval_seconds,
        "notification_interval_seconds": 30,
    }
    validate_config(config)
    path = config_root() / "client.json"
    mode_path = config_root() / "mode.json"
    marker_path = resolved_backup_dir / ".partyops-data-root.json"
    previous_files = {
        candidate: candidate.read_text(encoding="utf-8")
        if candidate.is_file()
        else None
        for candidate in (path, mode_path, marker_path)
    }
    try:
        previous_mode = (
            json.loads(previous_files[mode_path]).get("mode")
            if previous_files[mode_path]
            else ""
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        previous_mode = ""
    personal = config_root() / "personal.env"
    previous_personal = load_host_environment(personal) if personal.is_file() else {}
    personal_stopped = False
    host_deactivated: str | bool | None = None
    try:
        host_deactivated = deactivate_windows_host_for_user_mode()
        previous_data = previous_personal.get("PARTYOPS_DATA_DIR", "").strip()
        if os.name == "nt" and previous_data:
            personal_stopped = _stop_personal_process_for_data_migration(
                Path(previous_data),
                int(previous_personal.get("PARTYOPS_PORT", "18775")),
            )
        resolved_backup_dir.mkdir(parents=True, exist_ok=True)
        _write_data_root_marker(resolved_backup_dir, "client")
        _write_private(path, json.dumps(config, ensure_ascii=False, indent=2))
        write_mode_config("client")
        clear_windows_personal_autostart()
        finalize_windows_host_switch(host_deactivated)
        return path
    except Exception as original_error:
        rollback_errors: list[str] = []
        for candidate, content in previous_files.items():
            try:
                if content is None:
                    candidate.unlink(missing_ok=True)
                else:
                    _write_private(candidate, content)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"{candidate.name}：{exc}")
        if personal_stopped and previous_personal:
            try:
                _restart_previous_personal_process(previous_personal)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"个人模式：{exc}")
        try:
            if previous_mode == "personal":
                install_windows_personal_autostart()
            elif previous_mode == "client" and path.is_file():
                install_client_autostart(path)
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"自启动：{exc}")
        try:
            restore_windows_host_after_failed_switch(host_deactivated)
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"主机模式：{exc}")
        if rollback_errors:
            raise ValueError(
                "[MODE_SWITCH_ROLLBACK_FAILED] 协同模式配置失败且原模式未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        raise


def write_device_config(
    host_url: str,
    enrollment: dict[str, object],
    backup_dir: Path,
    *,
    device_name: str,
    shared_dir: Path | None = None,
    interval_seconds: int = 600,
) -> Path:
    """写入新版设备令牌配置；旧版 pairing_token 配置保持兼容但不具备文件协同权限。"""

    token = str(enrollment.get("device_token", "")).strip()
    device_id = str(enrollment.get("device_id", "")).strip()
    if not token or not device_id:
        raise ValueError("主机未返回完整设备凭据")
    if not 60 <= interval_seconds <= 86400:
        raise ValueError("灾备拉取间隔必须在 60 秒到 24 小时之间")
    roots: list[dict[str, object]] = []
    if shared_dir is not None:
        resolved = shared_dir.expanduser().resolve(strict=True)
        if not resolved.is_dir() or resolved.is_symlink():
            raise ValueError("共享目录必须是本机真实文件夹，不能是符号链接")
        roots.append(
            {
                "name": resolved.name,
                "local_path": str(resolved),
                "remote_key": secrets.token_urlsafe(12).replace("-", "_"),
            }
        )
    resolved_backup_dir = (
        _validate_windows_data_dir(backup_dir)
        if os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
        else backup_dir.expanduser().resolve()
    )
    values: dict[str, object] = {
        "mode": "client",
        "host_url": host_url.rstrip("/"),
        "agent_url": str(enrollment.get("agent_url", "")),
        "device_id": device_id,
        "device_token": token,
        "device_name": device_name.strip(),
        "backup_dir": str(resolved_backup_dir),
        "receive_dir": str((resolved_backup_dir / "接收文件").resolve()),
        "updates_dir": str((resolved_backup_dir / "updates").resolve()),
        "shared_roots": roots,
        "open_browser": True,
        "interval_seconds": interval_seconds,
        "notification_interval_seconds": 30,
        "scan_interval_seconds": 600,
        **device_metadata(),
    }
    private_key = str(enrollment.get("_private_key_pem", ""))
    certificate = str(enrollment.get("certificate_pem", ""))
    ca_certificate = str(enrollment.get("ca_certificate_pem", ""))
    pki_dir = config_root() / "pki"
    if private_key and certificate and ca_certificate:
        values.update(
            {
                "key_file": str((pki_dir / "device.key").resolve()),
                "certificate_file": str((pki_dir / "device.pem").resolve()),
                "ca_file": str((pki_dir / "ca.pem").resolve()),
            }
        )
    validate_config(values)
    path = config_root() / "client.json"
    mode_path = config_root() / "mode.json"
    marker_path = resolved_backup_dir / ".partyops-data-root.json"
    transaction_paths = [path, mode_path, marker_path]
    if private_key and certificate and ca_certificate:
        transaction_paths.extend(
            [pki_dir / "device.key", pki_dir / "device.pem", pki_dir / "ca.pem"]
        )
    previous_files = {
        candidate: candidate.read_text(encoding="utf-8")
        if candidate.is_file()
        else None
        for candidate in transaction_paths
    }
    try:
        previous_mode = (
            json.loads(previous_files[mode_path]).get("mode")
            if previous_files[mode_path]
            else ""
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        previous_mode = ""
    personal = config_root() / "personal.env"
    previous_personal = load_host_environment(personal) if personal.is_file() else {}
    personal_stopped = False
    host_deactivated: str | bool | None = None
    try:
        host_deactivated = deactivate_windows_host_for_user_mode()
        previous_data = previous_personal.get("PARTYOPS_DATA_DIR", "").strip()
        if os.name == "nt" and previous_data:
            personal_stopped = _stop_personal_process_for_data_migration(
                Path(previous_data),
                int(previous_personal.get("PARTYOPS_PORT", "18775")),
            )
        resolved_backup_dir.mkdir(parents=True, exist_ok=True)
        if private_key and certificate and ca_certificate:
            _write_private(pki_dir / "device.key", private_key)
            _write_private(pki_dir / "device.pem", certificate)
            _write_private(pki_dir / "ca.pem", ca_certificate, 0o644)
        _write_data_root_marker(resolved_backup_dir, "client")
        _write_private(path, json.dumps(values, ensure_ascii=False, indent=2))
        write_mode_config("client")
        clear_windows_personal_autostart()
        if ca_certificate:
            install_internal_ca(pki_dir / "ca.pem")
        finalize_windows_host_switch(host_deactivated)
        return path
    except Exception as original_error:
        rollback_errors: list[str] = []
        for candidate, content in previous_files.items():
            try:
                if content is None:
                    candidate.unlink(missing_ok=True)
                else:
                    _write_private(
                        candidate,
                        content,
                        0o644 if candidate.name == "ca.pem" else 0o600,
                    )
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"{candidate.name}：{exc}")
        if personal_stopped and previous_personal:
            try:
                _restart_previous_personal_process(previous_personal)
            except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
                rollback_errors.append(f"个人模式：{exc}")
        try:
            if previous_mode == "personal":
                install_windows_personal_autostart()
            elif previous_mode == "client" and path.is_file():
                install_client_autostart(path)
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"自启动：{exc}")
        try:
            restore_windows_host_after_failed_switch(host_deactivated)
        except Exception as exc:  # noqa: BLE001 - 汇总事务回滚诊断。
            rollback_errors.append(f"主机模式：{exc}")
        if rollback_errors:
            raise ValueError(
                "[MODE_SWITCH_ROLLBACK_FAILED] 设备配置失败且原模式未能完整恢复："
                + "；".join(rollback_errors)
            ) from original_error
        raise


def load_host_environment(path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            env[key] = shlex.split(value)[0] if value else ""
    frontend = runtime_root() / "frontend"
    if frontend.exists():
        env["PARTYOPS_FRONTEND_DIST"] = str(frontend)
    return env


def _executable(name: str) -> Path:
    root = runtime_root()
    candidates = [root / name, root / "PartyOps" / name]
    if sys.platform == "win32":
        candidates = [path.with_suffix(".exe") for path in candidates] + candidates
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"未找到运行程序：{name}")


def _spawn(
    command: list[str], log_path: Path, env: dict[str, str] | None = None
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    process = subprocess.Popen(  # noqa: S603 - 命令仅指向同包内固定可执行文件。
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    handle.close()
    return process


def install_internal_ca(ca_path: Path) -> None:
    """把局域网内部 CA 加入当前用户信任库，避免主机页面证书告警。"""

    if sys.platform == "win32":
        if not ca_path.is_file():
            return
        result = subprocess.run(
            ["certutil.exe", "-user", "-addstore", "Root", str(ca_path.resolve())],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise ValueError(
                "浏览器安全证书安装未完成。请确认当前账号允许写入用户证书库后重试。"
            )
        return
    if not sys.platform.startswith("linux"):
        return
    helper = runtime_root() / "install-internal-ca.sh"
    if not helper.is_file() or not ca_path.is_file():
        return
    try:
        result = subprocess.run(  # noqa: S603 - 固定随包脚本，参数由脚本再次限制和校验。
            [
                "pkexec",
                str(helper),
                "--desktop-user",
                getpass.getuser(),
                str(ca_path.resolve()),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "浏览器安全证书安装未完成。请确认 PolicyKit 管理员授权可用后重试。"
        ) from exc
    if result.returncode != 0:
        raise ValueError(
            "浏览器安全证书安装未完成。请关闭所有浏览器窗口，重新配置并同意管理员授权；"
            "业务数据和入网凭据不会因此丢失。"
        )


def _wait_and_install_ca(ca_path: Path) -> None:
    for _ in range(30):
        if ca_path.is_file():
            # 主机数据目录可由用户自由选择；先复制到固定配置目录，再交给
            # root helper，避免 helper 接受任意文件系统路径。
            trusted_copy = config_root() / "pki" / "ca.pem"
            _write_private(trusted_copy, ca_path.read_text(encoding="utf-8"))
            install_internal_ca(trusted_copy)
            return
        time.sleep(0.5)


def wait_for_host_health(
    host: str,
    port: int,
    tls: bool = False,
    timeout: float = 180.0,
    *,
    data_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    service_managed: bool = True,
) -> str:
    """通过本机回环轮询健康检查，就绪后返回可共享的展示 URL。

    主进程冷启动（冻结运行时解包 + 数据库迁移 + 证书生成 + 绑定端口）
    在 Windows 上通常需要 20–60 秒。此前向导/启动器在拉起服务后立刻
    打开浏览器，用户提交首次配置时服务尚未监听，出现
    “urlopen error [WinError 10061] 由于目标计算机积极拒绝”的错误。
    这里在打开浏览器前等待 /api/v1/health 返回 ok，杜绝该竞态。
    """
    scheme = "https" if tls else "http"
    advertised_url = f"{scheme}://{host}:{port}"
    probe_url = f"{scheme}://127.0.0.1:{port}"
    deadline = time.monotonic() + max(5.0, timeout)
    ca_file = (
        data_dir / "secrets" / "pki" / "ca.pem"
        if tls and data_dir is not None
        else None
    )
    context: ssl.SSLContext | None = None
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        if progress:
            progress("health_check")
        if service_managed and data_dir is not None:
            status = read_service_status(data_dir)
            if status and str(status.get("code", "")) in TERMINAL_CODES:
                code = str(status["code"])
                detail = str(status.get("detail", "")) or tail_service_log(data_dir)
                raise HostStartupError(
                    code, "PartyOps 主机进程启动失败。", detail=detail
                )
        try:
            if tls:
                if ca_file is not None and ca_file.is_file():
                    context = ssl.create_default_context(cafile=str(ca_file.resolve()))
                elif os.getenv("PARTYOPS_ENVIRONMENT") == "test":
                    context = ssl._create_unverified_context()  # nosec B323 - 仅测试夹具没有真实 CA。
                else:
                    raise ssl.SSLError("PartyOps 内部 CA 尚未生成")
            request = urllib.request.Request(f"{probe_url}/api/v1/health")
            with urllib.request.urlopen(  # nosec B310 - probe_url 固定为回环地址并只允许生成的 HTTP(S) 方案。
                request,
                timeout=3,
                context=context,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            from . import __version__

            expected_mode = "host" if service_managed else "personal"
            if health_payload_ready(
                payload,
                expected_version=__version__,
                expected_mode=expected_mode,
            ):
                if service_managed and data_dir is not None:
                    try:
                        write_service_status(data_dir, stage="ready")
                    except OSError:
                        # 服务已健康时，状态文件轮转失败不应反向阻断管理员创建。
                        pass
                if progress:
                    progress("ready")
                return advertised_url
            last_error = ValueError("健康检查返回内容无效")
        except (
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ssl.SSLError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = exc
        time.sleep(1.5)
    code = HEALTH_TIMEOUT
    detail = str(last_error or "")
    if (
        service_managed
        and os.name == "nt"
        and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
    ):
        state, service_detail = _query_windows_host_service()
        if state == "missing":
            code = SERVICE_MISSING
        elif state == "stopped":
            code = SERVICE_STOPPED
        if service_detail:
            detail = service_detail
    if service_managed and data_dir is not None:
        status = read_service_status(data_dir)
        if status and status.get("code"):
            code = str(status["code"])
            detail = str(status.get("detail", "")) or detail
        detail = detail or tail_service_log(data_dir)
    raise HostStartupError(
        code,
        (
            f"主机服务在 {timeout:.0f} 秒内未能就绪。"
            if service_managed
            else f"个人模式在 {timeout:.0f} 秒内未能就绪。"
        )
        + "系统没有打开未就绪地址，请点击重试或复制诊断。",
        detail=detail,
    ) from last_error


def _query_windows_host_service() -> tuple[str, str]:
    """返回 SCM 状态与脱敏后的原始诊断。"""

    result = subprocess.run(
        ["sc.exe", "queryex", "PartyOpsHost"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    detail = (result.stdout + result.stderr).strip()[-2000:]
    output = detail.lower()
    if (
        result.returncode == 1060
        or "service does not exist" in output
        or "指定的服务未安装" in output
    ):
        return "missing", detail
    if "stopped" in output or "已停止" in output:
        return "stopped", detail
    if "running" in output or "正在运行" in output:
        return "running", detail
    if "start_pending" in output or "启动挂起" in output:
        return "pending", detail
    if "stop_pending" in output or "停止挂起" in output:
        return "stopping", detail
    return "unknown", detail


def _start_windows_host_service(timeout: float = 60.0) -> None:
    """启动 Windows 主机服务，带存在性检查、状态诊断与重试。

    首次配置的常见失败原因：
    - 安装器刚注册完服务，SCM 尚未完全就绪，sc start 返回失败；
    - 冻结运行时（PyInstaller onedir）冷启动较慢，sc start 在 30 秒
      内返回 1053（服务未及时响应）；
    - 服务已被手动停止或处于异常状态。
    这里先查询服务是否安装，再按状态重试启动，最后给出可操作诊断，
    避免直接抛出笼统的“主机服务未能启动”。
    """

    state, detail = _query_windows_host_service()
    if state == "running":
        return
    if state == "missing":
        raise HostStartupError(
            SERVICE_MISSING,
            "未检测到“党建智办 PartyOps 主机服务”，请重新运行安装器完成修复安装。",
            detail=detail,
        )
    # 已安装但未运行：重试启动，容忍冷启动慢/SCM 未就绪。
    last_error: str | None = None
    deadline = time.monotonic() + max(5.0, timeout)
    max_attempts = max(3, math.ceil(max(5.0, timeout) / 3.0))
    attempt = 0
    while time.monotonic() < deadline and attempt < max_attempts:
        attempt += 1
        current, current_detail = _query_windows_host_service()
        if current == "running":
            return
        if current not in {"pending", "stopping"}:
            result = subprocess.run(
                ["sc.exe", "start", "PartyOpsHost"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            last_error = (
                result.stdout + result.stderr
            ).strip() or f"退出码 {result.returncode}"
        current, current_detail = _query_windows_host_service()
        if current == "running":
            return
        if current == "missing":
            raise HostStartupError(
                SERVICE_MISSING,
                "未检测到“党建智办 PartyOps 主机服务”，请重新运行安装器完成修复安装。",
                detail=current_detail,
            )
        time.sleep(1.0 if current == "pending" else 3.0)
    raise HostStartupError(
        SERVICE_STOPPED,
        "PartyOps 主机服务未进入运行状态。请使用配置向导重试或复制诊断信息。",
        detail=last_error or detail or "SCM 未返回可识别状态",
    )


def launch_host(config_path: Path) -> str:
    env = load_host_environment(config_path)
    host = env["PARTYOPS_HOST"]
    port = int(env["PARTYOPS_PORT"])
    tls = env.get("PARTYOPS_TLS_ENABLED", "").lower() == "true"
    install_host_autostart(config_path)
    if os.name == "nt":
        if os.getenv("PARTYOPS_ENVIRONMENT") == "test":
            _spawn(
                [str(_executable("partyops"))],
                Path(env["PARTYOPS_DATA_DIR"]) / "launcher.log",
                env,
            )
        else:
            _start_windows_host_service()
    else:
        _spawn(
            [str(_executable("partyops"))],
            Path(env["PARTYOPS_DATA_DIR"]) / "launcher.log",
            env,
        )
    # 先等服务就绪，再打开浏览器：消除首次配置提交时的 10061 连接竞态。
    url = wait_for_host_health(
        host,
        port,
        tls=tls,
        timeout=180.0,
        data_dir=Path(env["PARTYOPS_DATA_DIR"]),
    )
    # 证书由主机进程首次启动时生成；在打开浏览器前完成一次图形授权，
    # 避免用户首先看到“不受信任证书”警告。
    _wait_and_install_ca(Path(env["PARTYOPS_DATA_DIR"]) / "secrets" / "pki" / "ca.pem")
    return url


def launch_personal(config_path: Path) -> str:
    """按当前桌面账号启动本机专用进程，不注册服务、不开放局域网。"""

    env = load_host_environment(config_path)
    port = int(env["PARTYOPS_PORT"])
    data_dir = Path(env["PARTYOPS_DATA_DIR"])
    # 端口已由当前 PartyOps 占用时直接复用；若是其他程序占用则立即给出
    # 中文诊断，不能等待 180 秒后再让新手猜测原因。
    port_open = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            port_open = True
    except OSError:
        pass
    if port_open:
        try:
            return wait_for_host_health(
                "127.0.0.1",
                port,
                timeout=5.0,
                service_managed=False,
            )
        except HostStartupError as exc:
            raise HostStartupError(
                PORT_IN_USE,
                f"个人模式端口 {port} 已被其他程序占用，请更换端口后重试。",
                detail=exc.detail,
            ) from exc
    process = _spawn([str(_executable("partyops"))], data_dir / "launcher.log", env)
    _record_personal_process(data_dir, process)
    return wait_for_host_health(
        "127.0.0.1",
        port,
        timeout=180.0,
        service_managed=False,
    )


def install_host_autostart(config_path: Path) -> Path | None:
    """在 Linux 登录后自动恢复用户模式主机，不额外打开浏览器。"""

    if not sys.platform.startswith("linux"):
        return None
    start_script = runtime_root() / "start.sh"
    if not start_script.exists():
        return None

    def desktop_quote(value: Path) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    content = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=党建智办主机服务",
            (
                "Exec=env "
                f"PARTYOPS_ENV_FILE={desktop_quote(config_path.resolve())} "
                f"{desktop_quote(start_script)}"
            ),
            "Terminal=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
            f"X-PartyOps-Config={config_path.resolve()}",
            "",
        ]
    )
    path = config_root().parent / "autostart" / "partyops-host.desktop"
    _write_private(path, content)
    return path


def install_client_autostart(config_path: Path) -> Path | None:
    """在当前 Windows/UOS 桌面账号登录时恢复协同 Agent。"""

    if sys.platform == "win32":
        import winreg

        try:
            executable = _executable("PartyOpsAgent")
        except FileNotFoundError:
            # 源码运行和旧便携包仍使用 partyops-client；正式安装包使用
            # PartyOpsAgent.exe。这里只在固定随包名称之间兼容，不接受外部命令。
            executable = _executable("partyops-client")
        command = f'"{executable}" --config "{config_path.resolve()}" --no-open-browser'
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "PartyOpsAgent", 0, winreg.REG_SZ, command)
        return config_path
    if not sys.platform.startswith("linux"):
        return None
    executable = _executable("partyops-client")

    def desktop_quote(value: Path) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    content = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=党建智办灾备伴随进程",
            (
                f"Exec={desktop_quote(executable)} --config "
                f"{desktop_quote(config_path.resolve())} --no-open-browser"
            ),
            "Terminal=false",
            "NoDisplay=true",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )
    path = config_root().parent / "autostart" / "partyops-client.desktop"
    _write_private(path, content)
    return path


def launch_client(config_path: Path) -> str:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configure_ssl_context(config)
    host_url, token, destination = validate_config(config)
    install_client_autostart(config_path)
    _spawn(
        [
            str(_executable("partyops-client")),
            "--config",
            str(config_path),
            "--no-open-browser",
        ],
        destination / "client-agent.log",
    )
    agent_url = str(config.get("agent_url") or host_url).rstrip("/")
    if config.get("device_token"):
        # 入网完成页只有在 mTLS 设备通道真正收到首次心跳后才显示成功。
        # 这同时能立即发现相邻 Agent 端口未监听或被主机防火墙拦截，
        # 避免主机留下“已创建设备但始终离线”的半完成状态。
        for attempt in range(8):
            if send_device_heartbeat(
                agent_url,
                token,
                config,
                strict_identity=True,
            ):
                break
            if attempt < 7:
                time.sleep(0.5)
        else:
            port = urllib.parse.urlparse(agent_url).port or "相邻"
            raise ValueError(
                "入网凭据和证书已安全保存，但协同 Agent 尚未连通。"
                f"请确认主机服务正在监听设备端口 {port}，且主机防火墙允许可信局域网访问；"
                "修复后直接双击“党建智办”，不需要重新输入入网码。"
            )
    return create_browser_launch_url(host_url, agent_url, token)


def _record_wizard_failure(exc: Exception) -> str:
    """在本机保存技术诊断，浏览器只显示不含敏感内容的追踪编号。"""

    diagnostic_id = secrets.token_hex(6)
    log_path = config_root() / "wizard-errors.log"
    entry = (
        f"\n[{datetime.now(timezone.utc).isoformat()}] {diagnostic_id} "
        f"{type(exc).__name__}\n{traceback.format_exc()}"
    )
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
        if os.name != "nt":
            log_path.chmod(0o600)
    except OSError:
        pass
    return diagnostic_id


def resolve_host_url(
    host_url: str,
    token: str | None = None,
) -> tuple[str, dict[str, object]]:
    """探测主机协议；探测阶段绝不携带配对凭据。

    ``token`` 仅保留旧调用签名兼容，故意不写入请求。HTTPS 健康探测可能面对
    尚未信任的自签名证书，因此只能读取公开健康状态；随后设备入网会使用入网码
    中的 CA 指纹固定主机身份，再提交一次性入网凭据。
    """

    raw = host_url.strip().rstrip("/")
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("主机地址必须是无账号、无额外路径的 HTTP/HTTPS 局域网地址")
    if os.getenv("PARTYOPS_ENVIRONMENT") != "test":
        if parsed.hostname.lower() == "localhost":
            raise ValueError(
                "协同机不能使用回环地址；请填写主机在办公局域网中的真实 IP"
            )
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback or address.is_link_local or not address.is_private
        ):
            raise ValueError(
                "协同机不能使用回环地址或公网地址；请填写主机的真实局域网 IP"
            )
    normalized = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "", "", "", "")
    ).rstrip("/")
    candidates = [normalized]
    if parsed.scheme == "http":
        candidates.append(
            urllib.parse.urlunparse(
                parsed._replace(
                    scheme="https", path="", params="", query="", fragment=""
                )
            ).rstrip("/")
        )
    _ = token
    last_error: BaseException | None = None
    for candidate in dict.fromkeys(candidates):
        request = urllib.request.Request(
            f"{candidate}/api/v1/health",
        )
        try:
            context = (
                ssl._create_unverified_context()  # nosec B323 - 仅探测健康页；正式入网由入网码 CA 指纹固定。
                if urllib.parse.urlparse(candidate).scheme == "https"
                else None
            )
            with urllib.request.urlopen(  # nosec B310 - candidate 已被限制为无账号/路径的私有 HTTP(S) 主机。
                request,
                timeout=5,
                context=context,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                raise ValueError("主机健康检查返回内容无效")
            return candidate, payload
        except (
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ssl.SSLError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = exc
    raise ValueError(
        "无法连接主机。系统已核对主机地址并自动尝试 HTTPS；"
        "请确认 IP、18765 端口和主机服务状态。"
    ) from last_error


def check_host(host_url: str, token: str | None = None) -> dict[str, object]:
    """兼容旧调用，仅返回已验证主机的健康信息。"""

    _resolved_url, payload = resolve_host_url(host_url, token)
    return payload


def bootstrap_first_admin(
    service_url: str,
    *,
    username: str,
    display_name: str,
    password: str,
    ca_file: Path | None = None,
    bootstrap_token: str = "",
) -> None:
    """仅通过本机回环连接创建首位管理员，避免首次配置跨到业务登录页。"""

    normalized_username = username.strip().lower()
    normalized_display_name = display_name.strip()
    if len(normalized_display_name) < 2:
        raise ValueError("管理员姓名至少填写 2 个字")
    if not 3 <= len(normalized_username) <= 64 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-"
        for character in normalized_username
    ):
        raise ValueError("用户名需为 3—64 位英文字母、数字、点、短横线或下划线")
    if not 8 <= len(password) <= 128:
        raise ValueError("密码需要 8—128 个字符")
    parsed = urllib.parse.urlparse(service_url)
    if parsed.scheme not in {"http", "https"} or parsed.port is None:
        raise ValueError("主机服务地址无效，请返回重新配置")
    local_url = urllib.parse.urlunparse(
        (
            parsed.scheme,
            f"127.0.0.1:{parsed.port}",
            "/api/v1/bootstrap/host",
            "",
            "",
            "",
        )
    )
    payload = json.dumps(
        {
            "username": normalized_username,
            "display_name": normalized_display_name,
            "password": password,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        local_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-PartyOps-Bootstrap-Token": bootstrap_token,
        },
    )
    context: ssl.SSLContext | None = None
    if parsed.scheme == "https":
        if ca_file is not None and ca_file.is_file():
            context = ssl.create_default_context(cafile=str(ca_file.resolve()))
        elif os.getenv("PARTYOPS_ENVIRONMENT") == "test":
            context = ssl._create_unverified_context()  # nosec B323 - 测试夹具没有真实 CA。
        else:
            raise ValueError("PartyOps 内部 CA 尚未就绪，已拒绝发送管理员密码")
    try:
        with urllib.request.urlopen(  # nosec B310 - URL 在上方固定重写为 127.0.0.1 与固定 API 路径。
            request,
            timeout=15,
            context=context,
        ) as response:
            if response.status != 201:
                raise ValueError("主机未确认首位管理员，请重试")
    except urllib.error.HTTPError as exc:
        detail = "首位管理员创建失败，请核对字段后重试"
        try:
            problem = json.loads(exc.read().decode("utf-8"))
            detail = str(problem.get("detail") or problem.get("title") or detail)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        raise ValueError(detail) from exc


def render_admin_setup_page(csrf: str, service_url: str, error: str = "") -> str:
    """渲染首次配置最后一步；管理员创建成功后才离开配置向导。"""

    failure = (
        f'<div class="notice error" role="alert">{html.escape(error)}</div>'
        if error
        else ""
    )
    safe_url = html.escape(service_url)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>党建智办 · 创建首位管理员</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}main{{width:min(860px,94vw);margin:5vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}header{{padding:28px 38px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 32px SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:8px 0 0;color:#776f66}}.progress{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#ded4c8}}.progress span{{padding:12px 18px;color:#27613d;background:#edf3ea;font-size:12px;font-weight:600}}.progress span.active{{color:#8f1f17;background:#f8e9e6}}section{{padding:30px 38px}}.service-ok{{margin-bottom:22px;padding:14px 16px;color:#27613d;background:#eaf3ea;border-left:3px solid #39724d}}.service-ok strong,.service-ok small{{display:block}}.service-ok small{{margin-top:3px;color:#597261}}.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}}label{{display:block;margin-top:15px}}label:first-child{{grid-column:1/-1}}label span{{display:block;margin-bottom:6px;font-size:12px;font-weight:600}}label small{{display:block;margin-top:5px;color:#857c72}}input{{width:100%;height:44px;padding:0 12px;border:1px solid #cfc3b6;background:#fffdf8}}input:focus{{outline:2px solid #b4231830;border-color:#b42318}}button{{width:100%;height:48px;margin-top:24px;color:#fff;background:#b42318;border:0;font-weight:600;cursor:pointer}}.field-error{{min-height:20px;margin:4px 0 0;color:#9d2118;font-size:11px}}.notice{{margin:20px 38px 0;padding:12px 15px;border-left:3px solid}}.error{{background:#f8e9e7;border-color:#b42318}}footer{{padding:18px 38px;color:#776f66;background:#f1e9de;border-top:1px solid #ddd2c5}}@media(max-width:700px){{.form-grid,.progress{{grid-template-columns:1fr}}section,header{{padding-left:22px;padding-right:22px}}}}
</style></head><body><main><header><h1><b>党建</b>智办</h1><p>首次配置最后一步 · 创建团队首位管理员</p></header>
<div class="progress"><span>✓　角色已确认</span><span>✓　网络与主机已启动</span><span class="active">3　创建管理员并完成</span></div>{failure}
<section><div class="service-ok"><strong>主机服务连接正常</strong><small>{safe_url} · 管理员创建后才会进入业务登录页</small></div>
<form id="admin-form" method="post" novalidate><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="bootstrap_admin"><div class="form-grid">
<label><span>管理员姓名</span><input id="display-name" name="display_name" autocomplete="name" placeholder="例如：系统管理员" aria-describedby="display-name-error"><p id="display-name-error" class="field-error"></p></label>
<label><span>登录用户名</span><input id="username" name="username" autocomplete="username" placeholder="例如：admin" aria-describedby="username-error"><p id="username-error" class="field-error"></p></label>
<label><span>登录密码</span><input id="password" name="password" type="password" autocomplete="new-password" placeholder="至少 8 个字符" aria-describedby="password-error"><p id="password-error" class="field-error"></p><small>请使用单位可管理的强密码；系统不会在页面或日志中回显。</small></label></div>
<button>创建管理员并进入登录页</button></form></section><footer>此账号负责成员、设备、备份和更新。其他人员应在系统内单独创建账号，不要共用管理员密码。</footer></main>
<script>const form=document.getElementById('admin-form');const fields={{display_name:document.getElementById('display-name'),username:document.getElementById('username'),password:document.getElementById('password')}};const errors={{display_name:document.getElementById('display-name-error'),username:document.getElementById('username-error'),password:document.getElementById('password-error')}};form.addEventListener('submit',event=>{{Object.values(errors).forEach(node=>node.textContent='');const values={{display_name:fields.display_name.value.trim(),username:fields.username.value.trim(),password:fields.password.value}};const found={{}};if(values.display_name.length<2)found.display_name='管理员姓名至少填写 2 个字';if(!/^[A-Za-z0-9_.-]{{3,64}}$/.test(values.username))found.username='用户名需为 3—64 位英文字母、数字、点、短横线或下划线';if(values.password.length<8)found.password='密码至少需要 8 个字符';const first=Object.keys(found)[0];if(first){{event.preventDefault();Object.entries(found).forEach(([key,value])=>errors[key].textContent=value);fields[first].focus()}}}});</script></body></html>"""


def render_page(
    csrf: str,
    message: str = "",
    error: str = "",
    selected_mode: str = "",
) -> str:
    """渲染小白可完成的角色式首次配置向导。"""

    lan_addresses = discover_lan_addresses()
    addresses = [*lan_addresses, "127.0.0.1"]
    placeholder = (
        '<option value="" selected disabled>请选择与协同电脑同一网段的地址</option>'
        if len(lan_addresses) > 1
        else ""
    )
    options = placeholder + "".join(
        (
            f'<option value="{html.escape(address)}" '
            f"{'selected' if len(lan_addresses) <= 1 and index == 0 else ''}>"
            f"{html.escape(address)}"
            f"{' · 仅本机试用，不能协同' if address == '127.0.0.1' else ' · 检测到的本机地址'}"
            "</option>"
        )
        for index, address in enumerate(addresses)
    )
    notice = f'<div class="notice ok">{html.escape(message)}</div>' if message else ""
    failure = ""
    if error:
        safe_error = html.escape(error)
        failure = (
            f'<div class="notice error" role="alert"><strong>配置未完成</strong><br>'
            f'<span id="diagnostic-text">{safe_error}</span>'
            '<div class="diagnostic-actions">'
            '<button type="button" id="copy-diagnostic">复制诊断</button>'
            '<button type="button" id="open-host-logs">打开日志目录</button>'
            "</div></div>"
        )
    home = html.escape(str(Path.home()))
    host_data_dir = html.escape(str(installer_default_data_dir()))
    personal_data_dir = html.escape(str(initial_personal_data_dir()))
    initial_mode = (
        selected_mode if selected_mode in {"personal", "host", "client"} else ""
    )
    no_lan_notice = (
        '<div class="inline-warning">检测到多个本机地址。请在协同电脑查看自己的 IP，选择前三段相同的地址（例如 192.168.3.x）；系统不会替你猜测虚拟网卡。</div>'
        if len(lan_addresses) > 1
        else ""
        if lan_addresses
        else '<div class="inline-warning">尚未检测到办公局域网地址。可以仅本机试用，但其他电脑无法加入；请先连接办公网络后刷新。</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>党建智办 · 首次配置</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}
main{{width:min(1040px,94vw);margin:4vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}
header{{padding:28px 38px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 34px/1.2 SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:8px 0 0;color:#776f66}}
.progress{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#ded4c8;border-bottom:1px solid #ded4c8}}.progress span{{padding:12px 18px;color:#756d64;background:#f2ebe1;font-size:12px}}.progress span.active{{color:#8f1f17;background:#f8e9e6;font-weight:700}}
section{{padding:28px 38px}}.role-title{{margin:0 0 5px;font:600 22px SimSun,serif}}.role-subtitle{{margin:0 0 18px;color:#776f66}}
.role-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.role-card{{width:100%;min-height:174px;margin:0;padding:22px;text-align:left;color:#282522;background:#fffdf8;border:1px solid #cfc3b6;cursor:pointer}}.role-card:hover,.role-card.active{{border-color:#b42318;box-shadow:inset 0 0 0 1px #b42318}}.role-card b,.role-card span,.role-card small{{display:block}}.role-card b{{font:600 21px SimSun,serif}}.role-card span{{margin:8px 0 12px;color:#b42318;font-weight:600}}.role-card small{{color:#776f66}}.recommended{{display:inline-block;margin-bottom:8px;padding:2px 7px;color:#fff;background:#b42318;font-size:11px}}
.setup-panel{{margin:0 38px 30px;padding:28px;background:#fffdf8;border:1px solid #d8cec1}}.setup-panel[hidden]{{display:none}}.panel-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}}.panel-head h2{{margin:0;font:600 22px SimSun,serif}}.panel-head p{{margin:5px 0 0;color:#776f66}}.back{{width:auto;height:auto;margin:0;padding:4px 0;color:#8f1f17;background:transparent}}
.checklist{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 20px;padding:0;list-style:none}}.checklist li{{padding:10px 12px;color:#625c55;background:#f4ede3;border-left:2px solid #b42318;font-size:12px}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}}label{{display:block;margin:14px 0 0}}label.full{{grid-column:1/-1}}label span{{display:block;margin-bottom:6px;font-size:12px;font-weight:600}}label small{{display:block;margin-top:5px;color:#857c72}}
input,select{{width:100%;height:44px;padding:0 12px;border:1px solid #cfc3b6;background:#fffdf8}}input:focus,select:focus{{outline:2px solid #b4231830;border-color:#b42318}}button.primary{{width:100%;height:48px;margin-top:22px;color:white;background:#b42318;border:0;font-weight:600;cursor:pointer}}button[disabled]{{cursor:not-allowed;opacity:.45}}
.dir-row{{display:grid;grid-template-columns:1fr auto;gap:10px;align-items:center}}button.browse{{height:44px;padding:0 18px;color:#8f1f17;background:#fff8f5;border:1px solid #b42318;cursor:pointer}}
.test-row{{display:grid;grid-template-columns:1fr 1.2fr;gap:10px;align-items:end;grid-column:1/-1}}.test-button{{height:44px;margin:14px 0 0;color:#8f1f17;background:#fff8f5;border:1px solid #b42318;cursor:pointer}}.test-result{{min-height:44px;margin-top:14px;padding:10px 12px;color:#776f66;background:#f4ede3}}.test-result.ok{{color:#27613d;background:#eaf3ea}}.test-result.error{{color:#8f1f17;background:#f8e9e6}}
.inline-warning{{margin:12px 0;padding:10px 12px;color:#7a291f;background:#f8e9e6;border-left:3px solid #b42318}}
.notice{{margin:20px 38px 0;padding:12px 15px;border-left:3px solid}}.ok{{background:#eef4ed;border-color:#39724d}}.error{{background:#f8e9e7;border-color:#b42318}}
.diagnostic-actions{{display:flex;gap:10px;margin-top:10px}}.diagnostic-actions button{{height:36px;padding:0 14px;color:#8f1f17;background:#fff;border:1px solid #b42318;cursor:pointer}}
.startup-status{{display:none;margin:14px 0 0;padding:12px;background:#f4ede3;border-left:3px solid #b42318}}.startup-status.active{{display:block}}.startup-status ol{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin:8px 0 0;padding:0;list-style:none}}.startup-status li{{padding:8px;background:#fffdf8;color:#776f66;font-size:12px}}.startup-status li.active{{color:#8f1f17;font-weight:700}}
footer{{padding:18px 38px;color:#776f66;background:#f1e9de;border-top:1px solid #ddd2c5}}
@media(max-width:760px){{.role-grid,.form-grid,.test-row,.checklist{{grid-template-columns:1fr}}.progress{{grid-template-columns:1fr}}.setup-panel{{margin:0 18px 22px}}section,header{{padding-left:22px;padding-right:22px}}}}
</style></head><body><main><header><h1><b>党建</b>智办</h1><p>基层党建工作闭环协同系统 · PartyOps</p></header>
<div class="progress"><span class="active">1　选择这台电脑的角色</span><span>2　核对网络与配置</span><span>3　启动并确认连接</span></div>
{notice}{failure}
<section id="role-choice"><h2 class="role-title">第一步 · 这台电脑怎么使用</h2><p class="role-subtitle">只给自己用请选择“个人使用”，无需管理员授权；需要多台电脑协同办公时再选择主机或协同机。</p>
<div class="role-grid"><button type="button" class="role-card" data-role="personal"><em class="recommended">新手推荐</em><b>个人使用</b><span>无需管理员授权</span><small>只在本机打开，不开放局域网。数据库、附件、备份和本地智能都保存在当前账号选择的目录。</small></button>
<button type="button" class="role-card" data-role="host"><b>这是主机</b><span>保存全团队唯一业务数据</span><small>适合长期在线、地址稳定、由管理员维护的电脑。主机负责账号、备份、设备授权和文件中转，需要一次 UAC 授权。</small></button>
<button type="button" class="role-card" data-role="client"><b>这是协同机</b><span>加入已经配置好的主机</span><small>适合普通办公电脑。可登录业务系统、发布本机文件夹、浏览和下载已授权的团队文件。</small></button></div></section>
<section id="personal-panel" class="setup-panel" data-mode-panel="personal" hidden><div class="panel-head"><div><h2>配置个人使用</h2><p>只监听 127.0.0.1，不安装或启动主机服务，也不会弹出管理员授权。</p></div><button type="button" class="back">返回重选角色</button></div>
<ul class="checklist"><li>仅自己在本机使用</li><li>无需局域网协同</li><li>数据目录可自由选择</li></ul>
<form id="personal-form" method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="personal"><div class="form-grid">
<label><span>本机端口</span><input name="port" value="18775" inputmode="numeric"><small>通常无需修改；系统检测到占用时会立即说明。</small></label>
<label class="full"><span>个人数据与备份目录</span><div class="dir-row"><input id="personal-data-dir" name="data_dir" value="{personal_data_dir}" placeholder="例如 D:\\我的党建资料\\PartyOps 数据"><button id="browse-personal-data-dir" class="browse" type="button">浏览…</button></div><small>支持中文、空格和非 C 盘固定磁盘。只删除程序时会保留；选择彻底卸载时才会删除。</small></label></div>
<button class="primary">无需管理员授权，启动个人模式</button></form></section>
<section id="host-panel" class="setup-panel" data-mode-panel="host" hidden><div class="panel-head"><div><h2>配置主机</h2><p>完成后浏览器会打开主机，继续创建首位管理员账号。</p></div><button type="button" class="back">返回重选角色</button></div>
<ul class="checklist"><li>这台电脑会长期在线</li><li>已连接办公局域网</li><li>知道备份由谁负责</li></ul>{no_lan_notice}
<form id="host-form" method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="host"><div class="form-grid">
<label><span>主机局域网地址</span><select id="host-bind" name="host">{options}</select><small>协同电脑必须能访问该地址；真实办公网地址已优先显示。</small></label>
<label><span>服务端口</span><input name="port" value="18765" inputmode="numeric"><small>通常保持 18765；相邻端口 18766 用于安全设备通道。</small></label>
<label class="full"><span>数据与备份目录</span><div class="dir-row"><input id="data-dir" name="data_dir" value="{host_data_dir}" placeholder="例如 D:\\PartyOps-数据"><button id="browse-data-dir" class="browse" type="button">浏览…</button></div><small>业务数据库、附件、备份、证书、模型、缓存和运行日志都会保存在这里。建议选择本机数据盘目录（如 D:\\PartyOps-数据）；留空使用默认位置。</small></label></div>
<div id="loopback-warning" class="inline-warning" hidden>当前选择只能在这台电脑本机使用，其他电脑无法加入协同。若要协同，请连接办公网络并选择真实局域网地址。</div>
<div id="startup-status" class="startup-status" role="status" aria-live="polite"><strong id="startup-message">正在准备主机……</strong><ol><li data-stage="service">服务注册</li><li data-stage="child">子进程启动</li><li data-stage="port">端口监听</li><li data-stage="health">本机健康检查</li><li data-stage="ready">局域网地址就绪</li></ol></div>
<button id="host-submit" class="primary">确认配置并启动主机</button></form></section>
<section id="client-panel" class="setup-panel" data-mode-panel="client" hidden><div class="panel-head"><div><h2>加入已有主机</h2><p>先验证主机能访问，再提交一次性入网码；测试不会消耗入网码。</p></div><button type="button" class="back">返回重选角色</button></div>
<ul class="checklist"><li>与主机连接同一办公网络</li><li>已向管理员取得 10 分钟入网码</li><li>主机与本机版本一致</li></ul>
<form id="client-form" method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="client"><div class="form-grid">
<div class="test-row"><label><span>主机地址</span><input id="client-host-url" name="host_url" required placeholder="https://192.168.1.20:18765"><small>请从主机“设备协同 → 新增协同电脑”原样复制。</small></label><button id="test-client-host" class="test-button" type="button">先测试主机连接</button></div>
<div id="client-test-result" class="test-result">尚未测试。只有连接成功后才能继续安全入网。</div>
<label><span>本机设备名称</span><input name="device_name" required placeholder="例如：组织部-小王电脑"></label>
<label><span>一次性入网码</span><input name="token" required type="password" autocomplete="off"><small>入网码只使用一次，10 分钟后自动失效。</small></label>
<label><span>首次共享的本机文件夹（可选）</span><input name="shared_dir" placeholder="{home}/Documents/党建资料"><small>之后可在文件中心随时添加、移除或调整共享范围。</small></label>
<label><span>接收与灾备目录</span><input name="backup_dir" value="{home}/PartyOps-灾备副本"></label></div>
<button id="client-submit" class="primary" disabled>请先通过主机连接测试</button></form></section>
<footer>个人模式只在本机运行；主机保存团队唯一业务数据库；协同机只保存自己的配置、接收文件和获授权索引。任何模式都不会启用匿名共享。</footer>
</main><script>
const initialMode={json.dumps(initial_mode)};
const roleChoice=document.getElementById('role-choice');
const panels={{personal:document.getElementById('personal-panel'),host:document.getElementById('host-panel'),client:document.getElementById('client-panel')}};
const progressSteps=[...document.querySelectorAll('.progress span')];function setProgress(index){{progressSteps.forEach((step,key)=>step.classList.toggle('active',key===index))}}
function selectRole(role){{roleChoice.hidden=true;Object.entries(panels).forEach(([key,panel])=>panel.hidden=key!==role);setProgress(1);window.scrollTo({{top:0,behavior:'smooth'}})}}
document.querySelectorAll('[data-role]').forEach(button=>button.addEventListener('click',()=>selectRole(button.dataset.role)));
document.querySelectorAll('.back').forEach(button=>button.addEventListener('click',()=>{{Object.values(panels).forEach(panel=>panel.hidden=true);roleChoice.hidden=false;setProgress(0);window.scrollTo({{top:0,behavior:'smooth'}})}}));
const bind=document.getElementById('host-bind');const loopback=document.getElementById('loopback-warning');const hostSubmit=document.getElementById('host-submit');
function updateBindWarning(){{loopback.hidden=bind.value!=='127.0.0.1';hostSubmit.disabled=!bind.value}}bind.addEventListener('change',updateBindWarning);updateBindWarning();
const clientHost=document.getElementById('client-host-url');const testButton=document.getElementById('test-client-host');const testResult=document.getElementById('client-test-result');const clientSubmit=document.getElementById('client-submit');
let verifiedHost='';clientHost.addEventListener('input',()=>{{if(clientHost.value.trim()!==verifiedHost){{clientSubmit.disabled=true;clientSubmit.textContent='请先通过主机连接测试';testResult.className='test-result';testResult.textContent='地址已变化，请重新测试主机连接。'}}}});
testButton.addEventListener('click',async()=>{{const host=clientHost.value.trim();if(!host){{testResult.className='test-result error';testResult.textContent='请先填写主机地址。';return}}testButton.disabled=true;testResult.className='test-result';testResult.textContent='正在检查主机、端口和协议……';try{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'check_client',host_url:host}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const result=await response.json();if(!response.ok)throw new Error(result.error||'连接失败');verifiedHost=result.host_url;clientHost.value=result.host_url;clientSubmit.disabled=false;clientSubmit.textContent='连接已验证，安全入网并启动协同机';testResult.className='test-result ok';testResult.textContent=`连接成功：PartyOps ${{result.app_version||''}} 主机正常，可以继续入网。`}}catch(error){{verifiedHost='';clientSubmit.disabled=true;clientSubmit.textContent='请先通过主机连接测试';testResult.className='test-result error';testResult.textContent=error instanceof Error?error.message:'无法连接主机'}}finally{{testButton.disabled=false}}}});
const hostForm=document.getElementById('host-form');const startupStatus=document.getElementById('startup-status');const startupMessage=document.getElementById('startup-message');let startupPoll=null;
function showStartup(stage,message){{startupStatus.classList.add('active');startupMessage.textContent=message||'正在启动主机……';const order=['service','child','port','health','ready'];const index=Math.max(0,order.indexOf(stage));startupStatus.querySelectorAll('li').forEach((item,key)=>item.classList.toggle('active',key<=index));}}
async function pollStartup(){{try{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'host_status'}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const result=await response.json();if(response.ok)showStartup(result.ui_stage||'service',result.message||'正在启动主机……')}}catch(error){{}}}}
hostForm.addEventListener('submit',async event=>{{event.preventDefault();setProgress(2);hostSubmit.disabled=true;showStartup('service','正在确认主机服务……');startupPoll=setInterval(pollStartup,1000);try{{const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:new URLSearchParams(new FormData(hostForm))}});const page=await response.text();document.open();document.write(page);document.close();}}catch(error){{showStartup('service','向导连接中断，请重新打开配置向导。');hostSubmit.disabled=false}}finally{{if(startupPoll)clearInterval(startupPoll)}}}});
document.querySelectorAll('form:not(#host-form)').forEach(form=>form.addEventListener('submit',()=>setProgress(2)));
const browseButton=document.getElementById('browse-data-dir');const dataDirInput=document.getElementById('data-dir');
if(browseButton&&dataDirInput){{browseButton.addEventListener('click',async()=>{{browseButton.disabled=true;browseButton.textContent='选择中…';try{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'browse_data_dir'}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const result=await response.json();if(!response.ok)throw new Error(result.error||'选择失败');if(result.path)dataDirInput.value=result.path;}}catch(error){{}}finally{{browseButton.disabled=false;browseButton.textContent='浏览…'}}}})}};
const personalBrowse=document.getElementById('browse-personal-data-dir');const personalDataDir=document.getElementById('personal-data-dir');
if(personalBrowse&&personalDataDir){{personalBrowse.addEventListener('click',async()=>{{personalBrowse.disabled=true;personalBrowse.textContent='选择中…';try{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'browse_data_dir'}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const result=await response.json();if(!response.ok)throw new Error(result.error||'选择失败');if(result.path)personalDataDir.value=result.path;}}catch(error){{}}finally{{personalBrowse.disabled=false;personalBrowse.textContent='浏览…'}}}})}};
const diagnosticText=document.getElementById('diagnostic-text');const copyDiagnostic=document.getElementById('copy-diagnostic');if(copyDiagnostic&&diagnosticText){{copyDiagnostic.addEventListener('click',async()=>{{try{{await navigator.clipboard.writeText(diagnosticText.textContent||'');copyDiagnostic.textContent='已复制'}}catch(error){{copyDiagnostic.textContent='复制失败，请手动选中文本'}}}})}};
const openLogs=document.getElementById('open-host-logs');if(openLogs){{openLogs.addEventListener('click',async()=>{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'open_host_logs'}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});openLogs.textContent=response.ok?'已打开日志目录':'日志目录暂不可用'}})}};
if(initialMode)selectRole(initialMode);
</script></body></html>"""


def _choose_system_folder() -> Path | None:
    """优先调用 Windows/UOS 系统目录选择器；不可用时由手工路径入口兜底。"""

    if os.name == "nt":
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(title="选择允许 PartyOps 共享的文件夹")
            root.destroy()
            return Path(selected) if selected else None
        except Exception:  # noqa: BLE001 - 系统选择器缺失时安全回退到手工路径。
            return None
    for executable, arguments in (
        (
            "zenity",
            ["--file-selection", "--directory", "--title=选择 PartyOps 共享文件夹"],
        ),
        ("kdialog", ["--getexistingdirectory", str(Path.home())]),
    ):
        resolved = shutil.which(executable)
        if not resolved:
            continue
        result = subprocess.run(
            [resolved, *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    return None


def render_shared_root_manager(
    csrf: str,
    roots: list[dict[str, object]],
    message: str = "",
    error: str = "",
) -> str:
    notice = f'<div class="notice ok">{html.escape(message)}</div>' if message else ""
    failure = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    rows = []
    for root in roots:
        root_id = html.escape(str(root.get("root_id", "")))
        name = html.escape(str(root.get("name", "共享目录")))
        local_path = html.escape(str(root.get("local_path", "")))
        status = html.escape(str(root.get("approval_status", "pending")))
        note = html.escape(str(root.get("approval_note", "")))
        rows.append(
            f"""<article><div><b>{name}</b><span>{local_path}</span><small>状态：{status} · {note or "暂无审批说明"}</small></div>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="rename"><input type="hidden" name="root_id" value="{root_id}"><input name="name" value="{name}" aria-label="共享目录名称"><button>重命名</button></form>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="remove"><input type="hidden" name="root_id" value="{root_id}"><button class="danger">移除</button></form></article>"""
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>党建智办 · 管理共享文件夹</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}main{{width:min(980px,92vw);margin:5vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}header{{padding:28px 36px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 30px SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:7px 0 0;color:#776f66}}section{{padding:24px 36px}}.notice{{margin:18px 36px 0;padding:10px 14px;border-left:3px solid}}.ok{{background:#eef4ed;border-color:#39724d}}.error{{background:#f8e9e7;border-color:#b42318}}article{{display:grid;grid-template-columns:minmax(0,1fr) 250px 64px;gap:10px;align-items:center;padding:13px 0;border-bottom:1px solid #e3d9cd}}article b,article span,article small{{display:block}}article span{{color:#625c55;overflow-wrap:anywhere}}article small{{margin-top:3px;color:#8b8177}}input{{width:100%;height:38px;padding:0 10px;border:1px solid #cfc3b6;background:#fffdf8}}button{{height:38px;padding:0 15px;color:#fff;background:#b42318;border:0;cursor:pointer}}article form{{display:flex;gap:6px}}article form button{{flex:0 0 auto}}button.danger{{background:#6f312b}}.add-grid{{display:grid;grid-template-columns:1fr 1fr auto;gap:10px;margin-top:18px}}.actions{{display:flex;gap:10px;margin-top:18px}}.actions form{{margin:0}}.muted{{color:#776f66}}@media(max-width:760px){{article,.add-grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><h1><b>党建</b>智办 · 管理共享文件夹</h1><p>可重复添加、移除、重命名、查看审批状态并立即同步；本机绝对路径不会上传到主机。</p></header>{notice}{failure}<section><h2>本机共享目录</h2>{"".join(rows) or '<p class="muted">尚未添加共享目录。</p>'}
<form method="post" class="add-grid"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="add"><input name="local_path" placeholder="手工输入本机文件夹路径（留空使用系统选择器）"><input name="name" placeholder="显示名称（可选）"><button>添加共享目录</button></form>
<div class="actions"><form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="sync"><button>立即同步全部已批准目录</button></form><form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="close"><button class="danger">关闭工具</button></form></div></section></main></body></html>"""


def run_shared_root_manager(open_browser: bool = True, action_token: str = "") -> int:
    config_path = config_root() / "client.json"
    if not config_path.is_file():
        raise SystemExit("当前电脑尚未配置为协同机，请先完成安全入网。")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configure_ssl_context(config)
    host_url, token, _destination = validate_config(config)
    csrf = secrets.token_urlsafe(24)
    shutdown = threading.Event()
    pending_action_token = [action_token]

    def current_roots() -> list[dict[str, object]]:
        try:
            return refresh_shared_root_statuses(host_url, token, config, config_path)
        except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
            roots = config.get("shared_roots", [])
            return (
                [item for item in roots if isinstance(item, dict)]
                if isinstance(roots, list)
                else []
            )

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._send(render_shared_root_manager(csrf, current_roots()))

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 32_768)
                form = urllib.parse.parse_qs(
                    self.rfile.read(length).decode("utf-8"), keep_blank_values=True
                )
                value = lambda key: form.get(key, [""])[0]
                if not secrets.compare_digest(value("csrf"), csrf):
                    raise ValueError("管理页面已失效，请刷新后重试")
                action = value("action")
                message = ""
                if action == "add":
                    selected = (
                        Path(value("local_path")).expanduser()
                        if value("local_path").strip()
                        else _choose_system_folder()
                    )
                    if selected is None:
                        raise ValueError(
                            "未选择文件夹；也可以在输入框中手工填写完整路径"
                        )
                    added = add_shared_root(
                        host_url,
                        token,
                        config,
                        config_path,
                        selected,
                        value("name"),
                        pending_action_token[0],
                    )
                    pending_action_token[0] = ""
                    message = (
                        f"共享目录“{added.get('name')}”已发布，可在文件中心管理共享范围。"
                        if added.get("approval_status") == "approved"
                        else f"共享目录“{added.get('name')}”已登记，等待主机管理员审批。"
                    )
                elif action == "rename":
                    rename_shared_root(
                        host_url,
                        token,
                        config,
                        config_path,
                        value("root_id"),
                        value("name"),
                    )
                    message = "共享目录名称已更新。"
                elif action == "remove":
                    remove_shared_root(
                        host_url, token, config, config_path, value("root_id")
                    )
                    message = "共享目录已停用并从本机配置移除，旧索引将立即隐藏。"
                elif action == "sync":
                    refresh_shared_root_statuses(host_url, token, config, config_path)
                    indexed, errors = scan_and_upload_roots(
                        host_url, token, config, config_path
                    )
                    message = f"立即同步完成，共登记 {indexed} 个文件或目录；{errors} 项无法读取。"
                elif action == "close":
                    message = "共享目录管理工具已关闭。"
                    shutdown.set()
                else:
                    raise ValueError("未知的共享目录操作")
                self._send(
                    render_shared_root_manager(csrf, current_roots(), message=message)
                )
            except (
                OSError,
                ValueError,
                urllib.error.HTTPError,
                urllib.error.URLError,
            ) as exc:
                self._send(
                    render_shared_root_manager(csrf, current_roots(), error=str(exc)),
                    400,
                )

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    desktop_marker = _publish_linux_desktop_tool_url("shared-root-manager", url)
    if open_browser:
        webbrowser.open(url)
    server.timeout = 0.5
    try:
        while not shutdown.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        _clear_linux_desktop_tool_url(desktop_marker, url)
    return 0


def run_wizard(open_browser: bool = True, initial_mode: str = "") -> int:
    csrf = secrets.token_urlsafe(24)
    shutdown = threading.Event()
    startup_error = ""
    try:
        recover_pending_windows_host_switch()
    except Exception:
        startup_error = (
            "检测到上次模式切换未完成。请确认 Windows 管理员授权以恢复原主机，"
            "恢复完成前不要创建新配置。"
        )
    selected_mode = (
        initial_mode if initial_mode in {"personal", "host", "client"} else ""
    )
    host_setup: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, body: dict[str, object], status: int = 200) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802 - 标准库接口命名。
            self._send(
                render_page(
                    csrf,
                    selected_mode=selected_mode,
                    error=startup_error,
                )
            )

        def do_POST(self) -> None:  # noqa: N802 - 标准库接口命名。
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 32_768)
                form = urllib.parse.parse_qs(
                    self.rfile.read(length).decode("utf-8"), keep_blank_values=True
                )
                value = lambda key: form.get(key, [""])[0]
                if not secrets.compare_digest(value("csrf"), csrf):
                    raise ValueError("配置页面已失效，请刷新后重试")
                mode = value("mode")
                if mode == "check_client":
                    try:
                        host_url, health = resolve_host_url(value("host_url"))
                    except (ValueError, OSError, urllib.error.HTTPError) as exc:
                        self._send_json({"error": str(exc)}, 400)
                        return
                    self._send_json(
                        {
                            "host_url": host_url,
                            "status": health.get("status", ""),
                            "app_version": health.get("app_version", ""),
                            "mode": health.get("mode", ""),
                        }
                    )
                    return
                if mode == "browse_data_dir":
                    # 前端“浏览…”按钮：由向导进程调用系统目录选择器并回填，
                    # 浏览器本身拿不到本地绝对路径。
                    selected = _choose_system_folder()
                    self._send_json({"path": str(selected) if selected else ""})
                    return
                if mode in {"host_status", "open_host_logs"}:
                    config_path = (
                        Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
                        / "PartyOps"
                        / "partyops.env"
                    )
                    environment = (
                        load_host_environment(config_path)
                        if config_path.is_file()
                        else {}
                    )
                    data_dir = Path(
                        environment.get(
                            "PARTYOPS_DATA_DIR",
                            str(config_path.parent),
                        )
                    )
                    if mode == "open_host_logs":
                        log_dir = service_log_path(data_dir).parent
                        log_dir.mkdir(parents=True, exist_ok=True)
                        if os.name == "nt":
                            os.startfile(log_dir)  # type: ignore[attr-defined]
                        self._send_json({"opened": True, "path": str(log_dir)})
                        return
                    status = read_service_status(data_dir) or {}
                    stage = str(status.get("stage", ""))
                    ui_stage = "service"
                    message = "正在确认主机服务……"
                    if stage == "preparing":
                        ui_stage, message = "service", "正在准备防火墙与数据目录……"
                    elif stage == "child_running":
                        ui_stage, message = "child", "主进程已启动，正在等待端口监听……"
                        port = int(environment.get("PARTYOPS_PORT", "18765"))
                        try:
                            with socket.create_connection(
                                ("127.0.0.1", port), timeout=0.3
                            ):
                                ui_stage, message = (
                                    "port",
                                    "端口已监听，正在进行本机健康检查……",
                                )
                        except OSError:
                            pass
                    elif stage == "child_exited":
                        ui_stage, message = "child", "主进程已退出，正在生成诊断……"
                    elif stage == "ready":
                        ui_stage, message = "ready", "主机已就绪。"
                    self._send_json(
                        {
                            "ui_stage": ui_stage,
                            "message": message,
                            "code": status.get("code", ""),
                        }
                    )
                    return
                if mode == "personal":
                    path = write_personal_config(
                        Path(value("data_dir")),
                        int(value("port")),
                    )
                    url = launch_personal(path)
                    environment = load_host_environment(path)
                    host_setup["service_url"] = url
                    host_setup["ca_file"] = ""
                    host_setup["bootstrap_token"] = environment.get(
                        "PARTYOPS_BOOTSTRAP_TOKEN", ""
                    )
                    self._send(render_admin_setup_page(csrf, url))
                    return
                if mode == "host":
                    path = configure_host_config(
                        value("host"),
                        int(value("port")),
                        Path(value("data_dir")),
                    )
                    url = launch_host(path)
                    environment = load_host_environment(path) if path.exists() else {}
                    if environment.get("PARTYOPS_TLS_ENABLED", "").lower() == "true":
                        parsed = urllib.parse.urlparse(url)
                        url = urllib.parse.urlunparse(parsed._replace(scheme="https"))
                    host_setup["service_url"] = url
                    configured_data_dir = environment.get("PARTYOPS_DATA_DIR") or str(
                        Path(value("data_dir")).expanduser().resolve()
                    )
                    host_setup["ca_file"] = str(
                        Path(configured_data_dir) / "secrets" / "pki" / "ca.pem"
                    )
                    host_setup["bootstrap_token"] = environment.get(
                        "PARTYOPS_BOOTSTRAP_TOKEN", ""
                    )
                    self._send(render_admin_setup_page(csrf, url))
                    return
                elif mode == "bootstrap_admin":
                    service_url = host_setup.get("service_url", "")
                    if not service_url:
                        raise ValueError("主机配置状态已失效，请返回第一步重新配置")
                    bootstrap_first_admin(
                        service_url,
                        username=value("username"),
                        display_name=value("display_name"),
                        password=value("password"),
                        ca_file=(
                            Path(host_setup["ca_file"])
                            if host_setup.get("ca_file")
                            else None
                        ),
                        bootstrap_token=host_setup.get("bootstrap_token", ""),
                    )
                    self._redirect(service_url)
                    threading.Thread(
                        target=lambda: (time.sleep(1), shutdown.set()),
                        daemon=True,
                    ).start()
                    return
                elif mode == "client":
                    host_url, _health = resolve_host_url(value("host_url"))
                    device_name = value("device_name").strip()
                    if not device_name:
                        raise ValueError("必须填写本机设备名称，才能登记为协同电脑")
                    shared_text = value("shared_dir").strip()
                    backup_dir = Path(value("backup_dir")).expanduser()
                    if shared_text:
                        shared_dir = Path(shared_text).expanduser().resolve(strict=True)
                        if not shared_dir.is_dir() or shared_dir.is_symlink():
                            raise ValueError(
                                "共享目录必须是本机真实文件夹，不能是符号链接"
                            )
                    else:
                        shared_dir = None
                    # 在消费一次性入网码前完成所有本地路径检查，并把设备私钥、
                    # 请求身份和成功响应临时保存为 0600 文件。即使响应丢失或
                    # 向导中断，再次提交同一入网码也会恢复同一次请求。
                    pending_path = config_root() / "pending-enrollment.json"
                    enrollment = enroll_device(
                        host_url,
                        value("token"),
                        device_name,
                        pending_path=pending_path,
                    )
                    path = write_device_config(
                        host_url,
                        enrollment,
                        backup_dir,
                        device_name=device_name,
                        shared_dir=shared_dir,
                    )
                    url = launch_client(path)
                    pending_path.unlink(missing_ok=True)
                    message = f"协同终端已启动并连接：{url}"
                else:
                    raise ValueError("请选择个人使用、主机或协同终端")
                self._send(render_page(csrf, message=message, selected_mode=mode))
                threading.Thread(
                    target=lambda: (
                        time.sleep(1),
                        webbrowser.open(url),
                        shutdown.set(),
                    ),
                    daemon=True,
                ).start()
            except (ValueError, OSError, urllib.error.HTTPError) as exc:
                failed_mode = locals().get("mode", "")
                if failed_mode == "bootstrap_admin" and host_setup.get("service_url"):
                    self._send(
                        render_admin_setup_page(
                            csrf,
                            host_setup["service_url"],
                            error=str(exc),
                        ),
                        400,
                    )
                else:
                    self._send(
                        render_page(csrf, error=str(exc), selected_mode=failed_mode),
                        400,
                    )
            except Exception as exc:  # noqa: BLE001 - 本地 HTTP 边界必须返回完整诊断页。
                diagnostic_id = _record_wizard_failure(exc)
                self._send(
                    render_page(
                        csrf,
                        error=(
                            "配置未完成，系统已保留可恢复信息。请稍后重试；"
                            f"若仍失败，请在运行诊断中提供追踪编号 {diagnostic_id}。"
                        ),
                    ),
                    500,
                )

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
    desktop_marker = _publish_linux_desktop_tool_url("wizard", url)
    if open_browser:
        webbrowser.open(url)
    server.timeout = 0.5
    try:
        while not shutdown.is_set():
            server.handle_request()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        _clear_linux_desktop_tool_url(desktop_marker, url)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="党建智办主机/终端配置向导")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--manage-shared-roots", action="store_true")
    parser.add_argument("--action-uri", default="")
    parser.add_argument(
        "--initial-role", choices=("personal", "host", "client"), default=""
    )
    parser.add_argument("--privileged-host-config", action="store_true")
    parser.add_argument("--privileged-disable-host", action="store_true")
    parser.add_argument("--privileged-restore-host", action="store_true")
    parser.add_argument("--privileged-finalize-host-switch", action="store_true")
    parser.add_argument("--mode-switch-transaction", default="")
    parser.add_argument("--host", default="")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()
    if args.privileged_disable_host:
        if os.name != "nt" or not windows_is_admin():
            raise SystemExit("停用 Windows 主机角色需要管理员权限")
        _deactivate_windows_host_services_privileged(args.mode_switch_transaction)
        raise SystemExit(0)
    if args.privileged_restore_host:
        if os.name != "nt" or not windows_is_admin():
            raise SystemExit("恢复 Windows 主机角色需要管理员权限")
        _restore_windows_host_switch_privileged(args.mode_switch_transaction)
        raise SystemExit(0)
    if args.privileged_finalize_host_switch:
        if os.name != "nt" or not windows_is_admin():
            raise SystemExit("提交 Windows 模式切换需要管理员权限")
        _finalize_windows_host_switch_privileged(args.mode_switch_transaction)
        raise SystemExit(0)
    if args.privileged_host_config:
        if os.name != "nt" or not windows_is_admin():
            raise SystemExit("Windows 主机配置助手需要管理员权限")
        if not args.data_dir.strip():
            raise SystemExit("Windows 主机配置助手缺少 --data-dir，拒绝回退到 C 盘")
        write_host_config(
            args.host,
            args.port,
            Path(args.data_dir),
            write_user_mode=False,
        )
        _enable_windows_host_service_autostart()
        raise SystemExit(0)
    if args.manage_shared_roots:
        action_token = ""
        if args.action_uri:
            parsed = urllib.parse.urlparse(args.action_uri)
            if parsed.scheme != "partyops-client" or parsed.netloc != "manage-shares":
                raise SystemExit("无效的本机共享操作地址")
            action_token = parsed.path.strip("/")
            if not 32 <= len(action_token) <= 128 or any(
                char
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for char in action_token
            ):
                raise SystemExit("本机共享操作令牌无效")
        raise SystemExit(run_shared_root_manager(not args.no_browser, action_token))
    raise SystemExit(run_wizard(not args.no_browser, args.initial_role))


if __name__ == "__main__":
    main()
