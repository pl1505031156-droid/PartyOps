"""PartyOps 卸载数据清理器。

只从固定控制配置读取目录，并要求数据根存在 PartyOps 所有权标记；任何
符号链接、目录联接、磁盘根或系统目录都会使清理失败，避免高权限卸载器
被篡改配置后递归删除任意路径。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import ssl
import subprocess
import sys
import time
from pathlib import Path

from app.setup_wizard import _assert_managed_data_tree_has_no_reparse_points


APP_ID = "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A"
MARKER_NAME = ".partyops-data-root.json"


def _config_root() -> Path:
    return (
        Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "PartyOps"
    )


def _program_data() -> Path:
    return Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))


def _profile_paths() -> list[Path]:
    """返回本机已登记用户配置目录；不加载离线注册表配置单元。"""

    values = [Path(os.getenv("USERPROFILE", str(Path.home())))]
    if os.name == "nt":
        import winreg

        root = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    sid = winreg.EnumKey(key, index)
                    try:
                        with winreg.OpenKey(key, sid) as profile_key:
                            raw, _kind = winreg.QueryValueEx(
                                profile_key, "ProfileImagePath"
                            )
                        values.append(Path(os.path.expandvars(str(raw))))
                    except (FileNotFoundError, OSError):
                        continue
        except (FileNotFoundError, OSError):
            pass
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        try:
            resolved = value.resolve(strict=False)
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _user_config_roots() -> list[Path]:
    roots = [_config_root()]
    roots.extend(path / "AppData" / "Local" / "PartyOps" for path in _profile_paths())
    return list(dict.fromkeys(roots))


def _read_env_data_dir(path: Path) -> Path | None:
    if not path.is_file() or path.is_symlink():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "PARTYOPS_DATA_DIR":
            parts = shlex.split(value)
            return Path(parts[0]) if parts else None
    return None


def _read_client_data_dir(path: Path) -> Path | None:
    if not path.is_file() or path.is_symlink():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    value = str(payload.get("backup_dir", "")).strip()
    return Path(value) if value else None


def _assert_not_protected_path(path: Path) -> None:
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError(f"拒绝清理磁盘根或相对路径：{path}")
    protected_trees = []
    for name, fallback in (
        ("WINDIR", r"C:\Windows"),
        ("ProgramFiles", r"C:\Program Files"),
        ("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        protected_trees.append(Path(os.getenv(name) or fallback).resolve())
    profiles = tuple(_profile_paths())
    protected_roots = (
        *profiles,
        *(path.parent for path in profiles),
        _program_data().resolve(),
    )
    resolved = path.resolve(strict=True)
    for marker in protected_trees:
        if resolved == marker or marker in resolved.parents:
            raise ValueError(f"拒绝清理系统或程序目录：{resolved}")
    if resolved in protected_roots:
        raise ValueError(f"拒绝清理用户或共享目录根：{resolved}")


def _validate_managed_root(path: Path, expected_scope: str) -> Path:
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ValueError(f"PartyOps 数据目录不存在或不是本机真实目录：{path}")
    _assert_not_protected_path(path)
    resolved = path.resolve(strict=True)
    _assert_managed_data_tree_has_no_reparse_points(resolved)
    marker_path = resolved / MARKER_NAME
    if not marker_path.is_file() or marker_path.is_symlink():
        raise ValueError(f"数据目录缺少 PartyOps 所有权标记，已保留：{resolved}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    format_version = marker.get("format_version")
    scopes = (
        {str(marker.get("scope", ""))}
        if format_version == 1
        else {
            str(value) for value in marker.get("scopes", []) if isinstance(value, str)
        }
    )
    if (
        format_version not in {1, 2}
        or marker.get("product") != "PartyOps"
        or marker.get("app_id") != APP_ID
        or expected_scope not in scopes
        or not scopes.issubset({"host", "personal", "client"})
    ):
        raise ValueError(f"数据目录所有权标记不匹配，已保留：{resolved}")
    return resolved


def managed_roots(scope: str) -> list[Path]:
    roots: list[Path] = []
    if scope == "all":
        # 必须先完整预检两个范围再删除；同一数据根可能同时承载 host 与
        # personal/client，规范化去重后只删除一次，避免半卸载。
        roots.extend(managed_roots("user"))
        roots.extend(managed_roots("system"))
    elif scope == "user":
        for config_root in _user_config_roots():
            personal = _read_env_data_dir(config_root / "personal.env")
            if personal is not None:
                roots.append(_validate_managed_root(personal, "personal"))
            client = _read_client_data_dir(config_root / "client.json")
            if client is not None:
                roots.append(_validate_managed_root(client, "client"))
    elif scope == "system":
        host = _read_env_data_dir(_program_data() / "PartyOps" / "partyops.env")
        if host is not None:
            roots.append(_validate_managed_root(host, "host"))
    else:
        raise ValueError("卸载清理范围无效")
    return list(dict.fromkeys(roots))


def _remove_user_ca() -> None:
    ca_file = _config_root() / "pki" / "ca.pem"
    if os.name != "nt" or not ca_file.is_file() or ca_file.is_symlink():
        return
    try:
        der = ssl.PEM_cert_to_DER_cert(ca_file.read_text(encoding="ascii"))
        thumbprint = hashlib.sha1(der).hexdigest()  # nosec B324 - Windows 证书库唯一标识，不用于签名。
        subprocess.run(
            ["certutil.exe", "-user", "-delstore", "Root", thumbprint],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        # 证书删除失败不能诱导清理器去匹配宽泛名称；保留并由日志提示。
        print(
            "警告：当前用户证书库中的 PartyOps 内部 CA 未能自动删除。", file=sys.stderr
        )


def _stop_owned_user_processes() -> None:
    """只终止与清理器同安装目录的用户进程，避免按进程名误伤。"""

    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("无法枚举 PartyOps 进程")
    install_root = Path(sys.executable).resolve().parent
    expected = {
        os.path.normcase(str((install_root / name).resolve()))
        for name in ("PartyOps.exe", "PartyOpsAgent.exe", "PartyOpsLauncher.exe")
    }
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        available = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while available:
            if entry.szExeFile.lower() in {
                "partyops.exe",
                "partyopsagent.exe",
                "partyopslauncher.exe",
            }:
                handle = kernel32.OpenProcess(
                    0x1000 | 0x0001 | 0x00100000, False, entry.th32ProcessID
                )
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buffer, ctypes.byref(size)
                        ):
                            actual = os.path.normcase(str(Path(buffer.value).resolve()))
                            if actual in expected:
                                kernel32.TerminateProcess(handle, 0)
                                kernel32.WaitForSingleObject(handle, 10_000)
                    finally:
                        kernel32.CloseHandle(handle)
            available = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def _stop_system_services(timeout_seconds: float = 60.0) -> None:
    if os.name != "nt":
        return
    for service in ("PartyOpsHost", "PartyOpsUpdateService"):
        subprocess.run(
            ["sc.exe", "stop", service],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
    for service in ("PartyOpsHost", "PartyOpsUpdateService"):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["sc.exe", "query", service],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=15,
            )
            combined = (result.stdout + result.stderr).lower()
            if (
                result.returncode == 1060
                or "does not exist" in combined
                or "未安装" in combined
            ):
                break
            if "stopped" in combined or "已停止" in combined:
                break
            time.sleep(0.5)
        else:
            raise OSError(
                f"[UNINSTALL_SERVICE_STOP_TIMEOUT] {service} 在 60 秒内未停止"
            )


def _command_executable(command: str) -> Path | None:
    value = command.strip()
    if not value:
        return None
    if value.startswith('"'):
        end = value.find('"', 1)
        token = value[1:end] if end > 1 else ""
    else:
        token = value.split(maxsplit=1)[0]
    return Path(os.path.expandvars(token)) if token else None


def _remove_owned_autostarts() -> None:
    """清理所有已加载用户配置单元中确属当前安装目录的启动项。"""

    if os.name != "nt":
        return
    import winreg

    install_root = Path(sys.executable).resolve().parent
    expected = {
        "PartyOpsPersonal": (install_root / "PartyOpsLauncher.exe").resolve(),
        "PartyOpsAgent": (install_root / "PartyOpsAgent.exe").resolve(),
    }
    keys: list[tuple[object, str]] = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
    ]
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, "") as users:
            for index in range(winreg.QueryInfoKey(users)[0]):
                sid = winreg.EnumKey(users, index)
                if sid.endswith("_Classes"):
                    continue
                keys.append(
                    (
                        winreg.HKEY_USERS,
                        sid + r"\Software\Microsoft\Windows\CurrentVersion\Run",
                    )
                )
    except OSError:
        pass
    seen: set[tuple[int, str]] = set()
    for root, subkey in keys:
        identity = (int(root), subkey)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            with winreg.OpenKey(
                root, subkey, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
            ) as key:
                for value_name, expected_path in expected.items():
                    try:
                        command, _kind = winreg.QueryValueEx(key, value_name)
                    except FileNotFoundError:
                        continue
                    executable = _command_executable(str(command))
                    if executable is not None:
                        try:
                            owned = os.path.normcase(
                                str(executable.resolve())
                            ) == os.path.normcase(str(expected_path))
                        except OSError:
                            owned = False
                        if owned:
                            winreg.DeleteValue(key, value_name)
        except (FileNotFoundError, PermissionError, OSError):
            continue


def _remove_fixed_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise ValueError(f"拒绝清理被替换为链接的 PartyOps 配置目录：{path}")
    _assert_managed_data_tree_has_no_reparse_points(path)
    shutil.rmtree(path)


def execute(scope: str, *, check_only: bool) -> None:
    if scope == "runtime":
        if not check_only:
            _stop_owned_user_processes()
            _remove_owned_autostarts()
            # “仅删除程序”仍应移除由安装器/更新服务生成的系统缓存与日志，
            # 否则每次卸载会遗留一份完整安装器。业务配置与用户选定数据根
            # 位于 ProgramData/PartyOps 或自定义目录，不在此固定运行时根中。
            _stop_system_services()
            _remove_fixed_tree(_program_data() / "PartyOps-System")
        return
    roots = managed_roots(scope)
    if check_only:
        return
    if scope in {"user", "all"}:
        _stop_owned_user_processes()
        _remove_owned_autostarts()
        _remove_user_ca()
    if scope in {"system", "all"}:
        _stop_system_services()
    for root in roots:
        shutil.rmtree(root)
    if scope in {"user", "all"}:
        for config_root in _user_config_roots():
            _remove_fixed_tree(config_root)
    if scope in {"system", "all"}:
        _remove_fixed_tree(_program_data() / "PartyOps")
        _remove_fixed_tree(_program_data() / "PartyOps-System")


def main() -> int:
    parser = argparse.ArgumentParser(description="PartyOps 安全卸载数据清理器")
    parser.add_argument(
        "--scope", choices=("runtime", "user", "system", "all"), required=True
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        execute(args.scope, check_only=args.check)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[UNINSTALL_DATA_CLEANUP_FAILED] {exc}", file=sys.stderr)
        return 2
    print("PartyOps 数据清理检查完成。" if args.check else "PartyOps 本机数据已清理。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
