"""macOS 独立更新 helper：重读受控配置后执行 PKG 健康确认与回滚。"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path


ALLOWED_ENVIRONMENT_KEYS = {
    "PARTYOPS_MODE",
    "PARTYOPS_DATA_DIR",
    "PARTYOPS_HOST",
    "PARTYOPS_BIND_HOST",
    "PARTYOPS_ADVERTISE_HOST",
    "PARTYOPS_PORT",
    "PARTYOPS_AGENT_PORT",
    "PARTYOPS_TLS_ENABLED",
    "PARTYOPS_STRICT_SQLITE",
    "PARTYOPS_UPDATE_PUBLIC_KEY",
}
APP_ID = "1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A"


def _config_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "PartyOps" / "Config"


def _has_no_symlink(path: Path, stop: Path) -> bool:
    cursor = path
    while True:
        if cursor.is_symlink():
            return False
        if cursor == stop:
            return True
        if cursor.parent == cursor or stop not in cursor.parents:
            return False
        cursor = cursor.parent


def _controlled_environment() -> dict[str, str]:
    root = _config_root().resolve()
    mode_path = root / "mode.json"
    if not mode_path.is_file() or not _has_no_symlink(mode_path, root):
        raise RuntimeError("macOS 更新模式配置缺失或包含链接")
    mode_payload = json.loads(mode_path.read_text(encoding="utf-8"))
    mode = str(mode_payload.get("mode") or "") if isinstance(mode_payload, dict) else ""
    if mode not in {"host", "personal"}:
        raise RuntimeError("macOS 更新只允许当前主机或个人模式执行")
    default_name = "personal.env" if mode == "personal" else "partyops.env"
    config_path = Path(
        str(mode_payload.get("config_path") or root / default_name)
    ).expanduser().resolve()
    if root not in config_path.parents or not config_path.is_file() or not _has_no_symlink(
        config_path, root
    ):
        raise RuntimeError("macOS 更新配置不属于当前用户受控目录")
    values: dict[str, str] = {}
    for line in config_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if separator and key in ALLOWED_ENVIRONMENT_KEYS:
            values[key] = shlex.split(raw)[0] if raw else ""
    data_text = values.get("PARTYOPS_DATA_DIR", "").strip()
    data_dir = Path(data_text).expanduser().resolve() if data_text else Path()
    marker = data_dir / ".partyops-data-root.json"
    if (
        values.get("PARTYOPS_MODE") != mode
        or not data_text
        or not data_dir.is_dir()
        or data_dir.is_symlink()
        or not marker.is_file()
        or marker.is_symlink()
    ):
        raise RuntimeError("macOS 更新数据目录未通过所有权边界校验")
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    scopes = (
        {str(marker_payload.get("scope") or "")}
        if marker_payload.get("format_version") == 1
        else {
            str(value)
            for value in marker_payload.get("scopes", [])
            if isinstance(value, str)
        }
        if marker_payload.get("format_version") == 2
        else set()
    )
    if (
        marker_payload.get("product") != "PartyOps"
        or marker_payload.get("app_id") != APP_ID
        or mode not in scopes
        or not scopes.issubset({"host", "personal", "client"})
    ):
        raise RuntimeError("macOS 更新数据目录标记与当前角色不一致")
    values.update(
        {
            "PARTYOPS_MODE": mode,
            "PARTYOPS_DATA_DIR": str(data_dir),
            "PARTYOPS_ENVIRONMENT": "production",
            "PARTYOPS_STRICT_SQLITE": "true",
        }
    )
    return values


device_update_requested = "--macos-install-package" in sys.argv[1:]
device_update_mode = (
    len(sys.argv) == 3 and sys.argv[1] == "--macos-install-package"
)
if device_update_requested and not device_update_mode:
    raise SystemExit("macOS 协同更新参数组合无效")

for environment_key in tuple(os.environ):
    if environment_key.startswith("PARTYOPS_"):
        os.environ.pop(environment_key, None)
if device_update_mode:
    # 协同更新不读取主机数据库，只验证安装目录根公钥、签名更新包与 PKG。
    os.environ["PARTYOPS_ENVIRONMENT"] = "production"
else:
    os.environ.update(_controlled_environment())

from app.update_executor import main  # noqa: E402 - 必须先重建最小受控环境。


if __name__ == "__main__":
    main()
