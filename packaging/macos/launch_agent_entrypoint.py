"""macOS LaunchAgent 入口：按受控 mode.json 恢复个人、主机或协同进程。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path


SUPPORTED_MODES = {"host", "personal", "client"}


def _config_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "PartyOps" / "Config"


def _runtime_root() -> Path:
    return Path(sys.executable).resolve().parent


def _read_mode() -> dict[str, object]:
    path = _config_root() / "mode.json"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("PartyOps 运行模式尚未配置")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise RuntimeError("PartyOps 运行模式配置已损坏")
    return payload


def _read_environment(path: Path) -> dict[str, str]:
    # 登录项不得继承调用者额外注入的 PARTYOPS_ 配置；运行参数
    # 只能来自向导写入的当前角色配置文件。
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PARTYOPS_")
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, raw = line.partition("=")
        if separator and key.startswith("PARTYOPS_"):
            environment[key] = shlex.split(raw)[0] if raw else ""
    return environment


def _write_personal_marker(data_dir: Path, executable: Path) -> None:
    marker = data_dir / ".partyops-personal-process.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "format_version": 1,
                "pid": os.getpid(),
                "executable": str(executable.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, marker)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(SUPPORTED_MODES), required=True)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--browser-url-file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        runtime = _runtime_root()
        required = (runtime / "partyops", runtime / "partyops-client")
        if any(not path.is_file() for path in required):
            raise RuntimeError("macOS LaunchAgent 冻结运行时不完整")
        print("PartyOps macOS LaunchAgent 入口自检通过。")
        return 0

    mode_payload = _read_mode()
    configured_mode = str(mode_payload.get("mode") or "")
    if configured_mode != args.mode:
        raise RuntimeError(
            f"启动项模式 {args.mode} 与当前配置 {configured_mode or '空'} 不一致"
        )
    runtime = _runtime_root()
    if args.mode == "client":
        config_path = Path(
            str(mode_payload.get("config_path") or _config_root() / "client.json")
        ).expanduser().resolve()
        executable = runtime / "partyops-client"
        command = [str(executable), "--config", str(config_path)]
        if not args.open_browser:
            command.append("--no-open-browser")
        if args.browser_url_file is not None:
            command.extend(["--browser-url-file", str(args.browser_url_file.resolve())])
        environment = os.environ.copy()
    else:
        default_name = "personal.env" if args.mode == "personal" else "partyops.env"
        config_path = Path(
            str(mode_payload.get("config_path") or _config_root() / default_name)
        ).expanduser().resolve()
        executable = runtime / "partyops"
        command = [str(executable)]
        environment = _read_environment(config_path)
        if environment.get("PARTYOPS_MODE") != args.mode:
            raise RuntimeError("PartyOps 环境配置与启动角色不一致")
        if args.mode == "personal":
            data_dir = Path(environment["PARTYOPS_DATA_DIR"]).expanduser().resolve()
            _write_personal_marker(data_dir, executable)

    if not executable.is_file() or executable.is_symlink():
        raise RuntimeError(f"PartyOps 冻结程序不存在：{executable}")
    os.execve(executable, command, environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
