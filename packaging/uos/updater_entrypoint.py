"""PyInstaller 系统级更新入口。

此文件在导入应用配置前先清除调用者可控的 ``PARTYOPS_*`` 环境变量。
系统服务会从受保护配置重新发现主机；Windows 个人模式只保留经数据根
所有权标记约束的最小运行上下文，更新公钥永远不从用户环境继承。
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
from pathlib import Path


APP_ID = "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A"
WINDOWS_SYSTEM_KEYS = {
    "PARTYOPS_DATA_DIR",
    "PARTYOPS_PORT",
    "PARTYOPS_TLS_ENABLED",
    "PARTYOPS_TLS_CLIENT_CA_FILE",
}


def _is_link_or_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def _path_tree_has_no_reparse_points(path: Path) -> bool:
    """拒绝配置、数据根及其祖先中的联接，避免 SYSTEM 跟随重定向。"""

    cursor = path
    while True:
        if _is_link_or_reparse_point(cursor):
            return False
        if cursor.parent == cursor:
            return True
        cursor = cursor.parent


def _windows_program_data() -> Path | None:
    """从 HKLM 获取系统 ProgramData；不能信任调用者可伪造的环境变量。"""

    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            0,
            winreg.KEY_READ,
        ) as key:
            raw = str(winreg.QueryValueEx(key, "Common AppData")[0]).strip()
    except (OSError, ValueError):
        return None
    candidate = Path(os.path.expandvars(raw))
    return candidate.resolve(strict=False) if candidate.is_absolute() else None


def _windows_is_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def _windows_config_is_protected(path: Path) -> bool:
    """配置可供普通用户读取，但只有 SYSTEM/管理员能够写入或替换。"""

    if os.name != "nt":
        return False
    try:
        import ntsecuritycon
        import win32security

        trusted = {
            "S-1-5-18",
            "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
        }
        write_mask = (
            ntsecuritycon.DELETE
            | ntsecuritycon.WRITE_DAC
            | ntsecuritycon.WRITE_OWNER
            | ntsecuritycon.GENERIC_WRITE
            | ntsecuritycon.GENERIC_ALL
            | ntsecuritycon.FILE_WRITE_DATA
            | ntsecuritycon.FILE_APPEND_DATA
            | ntsecuritycon.FILE_WRITE_EA
            | ntsecuritycon.FILE_WRITE_ATTRIBUTES
            | ntsecuritycon.FILE_DELETE_CHILD
        )
        for candidate in (path.parent, path):
            descriptor = win32security.GetFileSecurity(
                str(candidate),
                win32security.OWNER_SECURITY_INFORMATION
                | win32security.DACL_SECURITY_INFORMATION,
            )
            owner = win32security.ConvertSidToStringSid(
                descriptor.GetSecurityDescriptorOwner()
            )
            if owner not in trusted:
                return False
            dacl = descriptor.GetSecurityDescriptorDacl()
            if dacl is None:
                return False
            for index in range(dacl.GetAceCount()):
                ace = dacl.GetAce(index)
                ace_type, ace_flags = ace[0]
                if ace_flags & 0x08 or ace_type not in {
                    win32security.ACCESS_ALLOWED_ACE_TYPE,
                    getattr(win32security, "ACCESS_ALLOWED_OBJECT_ACE_TYPE", 5),
                }:
                    continue
                sid = win32security.ConvertSidToStringSid(ace[-1])
                if sid not in trusted and int(ace[1]) & write_mask:
                    return False
    except (ImportError, OSError, AttributeError, TypeError, ValueError):
        return False
    return True


def _windows_data_path_is_protected(path: Path) -> bool:
    """复核自定义数据根及祖先不可被普通用户写入、删除或替换。"""

    if os.name != "nt":
        return False
    try:
        import win32security

        trusted = {
            "S-1-5-18",
            "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
        }
        delete = 0x00010000
        target_write_mask = (
            delete
            | 0x00040000
            | 0x00080000
            | 0x10000000
            | 0x40000000
            | 0x00000002
            | 0x00000004
            | 0x00000010
            | 0x00000040
            | 0x00000100
        )
        resolved = path.resolve(strict=True)
        cursor = resolved
        while True:
            descriptor = win32security.GetFileSecurity(
                str(cursor),
                win32security.OWNER_SECURITY_INFORMATION
                | win32security.DACL_SECURITY_INFORMATION,
            )
            owner = win32security.ConvertSidToStringSid(
                descriptor.GetSecurityDescriptorOwner()
            )
            dacl = descriptor.GetSecurityDescriptorDacl()
            if owner not in trusted or dacl is None:
                return False
            is_target = cursor == resolved
            volume_root = cursor.parent == cursor
            for index in range(dacl.GetAceCount()):
                ace = dacl.GetAce(index)
                ace_type, ace_flags = ace[0]
                if ace_flags & 0x08 or ace_type not in {
                    win32security.ACCESS_ALLOWED_ACE_TYPE,
                    getattr(win32security, "ACCESS_ALLOWED_OBJECT_ACE_TYPE", 5),
                }:
                    continue
                sid = win32security.ConvertSidToStringSid(ace[-1])
                if sid in trusted:
                    continue
                mask = int(ace[1])
                unsafe = bool(mask & 0x00000040)
                if not volume_root:
                    unsafe = unsafe or bool(mask & delete)
                if is_target:
                    unsafe = unsafe or bool(mask & target_write_mask)
                if unsafe:
                    return False
            if volume_root:
                return True
            cursor = cursor.parent
    except (ImportError, OSError, AttributeError, TypeError, ValueError):
        return False


def _read_partyops_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if not separator or key not in WINDOWS_SYSTEM_KEYS | {"PARTYOPS_MODE"}:
            continue
        try:
            tokens = shlex.split(raw)
        except ValueError:
            continue
        values[key] = tokens[0] if tokens else ""
    return values


def _validated_windows_system_environment(
    *, program_data: Path | None = None
) -> dict[str, str]:
    """系统更新入口独立重读受保护配置，不继承服务包装器传入的值。"""

    root = program_data or _windows_program_data()
    if root is None or not _windows_is_elevated():
        return {}
    root = root.resolve(strict=False)
    config_path = root / "PartyOps" / "partyops.env"
    mode_path = config_path.parent / "mode.json"
    if (
        not config_path.is_file()
        or not mode_path.is_file()
        or not _path_tree_has_no_reparse_points(config_path)
        or not _path_tree_has_no_reparse_points(mode_path)
        or not _windows_config_is_protected(config_path)
        or not _windows_config_is_protected(mode_path)
        or not _windows_data_path_is_protected(config_path.parent)
    ):
        return {}
    try:
        mode = json.loads(mode_path.read_text(encoding="utf-8"))
        values = _read_partyops_environment(config_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    raw_data_dir = values.get("PARTYOPS_DATA_DIR", "").strip()
    data_dir = Path(raw_data_dir).expanduser() if raw_data_dir else Path()
    if (
        not isinstance(mode, dict)
        or mode.get("mode") != "host"
        or values.get("PARTYOPS_MODE") != "host"
        or not raw_data_dir
        or not data_dir.is_absolute()
        or not data_dir.is_dir()
        or not _path_tree_has_no_reparse_points(data_dir)
        or not _windows_data_path_is_protected(data_dir)
    ):
        return {}
    sanitized = {
        key: value for key, value in values.items() if key in WINDOWS_SYSTEM_KEYS
    }
    sanitized.update(
        {
            "PARTYOPS_MODE": "host",
            "PARTYOPS_DATA_DIR": str(data_dir.resolve()),
            "PARTYOPS_ENVIRONMENT": "production",
            "PARTYOPS_STRICT_SQLITE": "true",
        }
    )
    return sanitized


def _validated_personal_environment(environ: dict[str, str]) -> dict[str, str]:
    if environ.get("PARTYOPS_MODE") != "personal":
        return {}
    raw = environ.get("PARTYOPS_DATA_DIR", "").strip()
    if not raw:
        return {}
    data_dir = Path(raw).expanduser()
    if not data_dir.is_absolute() or data_dir.is_symlink() or not data_dir.is_dir():
        return {}
    marker_path = data_dir / ".partyops-data-root.json"
    if not marker_path.is_file() or marker_path.is_symlink():
        return {}
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    scopes = (
        {str(marker.get("scope", ""))}
        if marker.get("format_version") == 1
        else {
            str(value) for value in marker.get("scopes", []) if isinstance(value, str)
        }
        if marker.get("format_version") == 2
        else set()
    )
    if (
        marker.get("product") != "PartyOps"
        or marker.get("app_id") != APP_ID
        or "personal" not in scopes
        or not scopes.issubset({"host", "personal", "client"})
    ):
        return {}
    try:
        port = int(environ.get("PARTYOPS_PORT", "18775"))
    except ValueError:
        return {}
    if not 1024 <= port <= 65534:
        return {}
    return {
        "PARTYOPS_MODE": "personal",
        "PARTYOPS_DATA_DIR": str(data_dir.resolve()),
        "PARTYOPS_HOST": "127.0.0.1",
        "PARTYOPS_BIND_HOST": "127.0.0.1",
        "PARTYOPS_ADVERTISE_HOST": "127.0.0.1",
        "PARTYOPS_PORT": str(port),
        "PARTYOPS_AGENT_PORT": str(port + 1),
        "PARTYOPS_TLS_ENABLED": "false",
        "PARTYOPS_STRICT_SQLITE": "true",
    }


def _argument_value(argv: list[str], name: str) -> str:
    """提权入口只读取一次固定参数，重复、缺失或无值均拒绝。"""

    positions = [index for index, value in enumerate(argv) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        return ""
    return argv[positions[0] + 1]


def _prepare_privileged_environment(argv: list[str], environ: dict[str, str]) -> None:
    system = (
        _validated_windows_system_environment()
        if "--windows-system-service" in argv
        else {}
    )
    if "--windows-system-service" in argv and not system:
        raise RuntimeError("Windows 更新服务配置未通过权限与路径校验，已拒绝启动")
    if "--linux-personal-transaction" in argv:
        # pkexec 会主动丢弃普通用户环境；显式参数仍须通过个人数据根标记与
        # 回环端口规则，不能直接提升为特权配置。
        personal = _validated_personal_environment(
            {
                "PARTYOPS_MODE": "personal",
                "PARTYOPS_DATA_DIR": _argument_value(argv, "--personal-data-dir"),
                "PARTYOPS_PORT": _argument_value(argv, "--personal-port"),
            }
        )
        if not personal:
            raise RuntimeError("Linux 个人更新上下文未通过路径与所有权标记校验")
    elif "--personal-run-id" in argv or "--linux-personal-run-id" in argv:
        personal = _validated_personal_environment(environ)
    else:
        personal = {}
    for key in tuple(environ):
        if key.startswith("PARTYOPS_"):
            environ.pop(key, None)
    environ.update(system or personal)
    environ["PARTYOPS_ENVIRONMENT"] = "production"


_prepare_privileged_environment(sys.argv[1:], os.environ)

from app.update_executor import main  # noqa: E402 - 必须先收敛提权环境再导入配置。


if __name__ == "__main__":
    main()
