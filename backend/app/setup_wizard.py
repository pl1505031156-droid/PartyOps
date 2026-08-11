"""便携版首次配置向导：选择主机或终端，不创建第二份业务数据库。"""

from __future__ import annotations

import argparse
import getpass
import html
import http.client
import ipaddress
import json
import os
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
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


def _write_private(path: Path, content: str, mode: int = 0o600) -> None:
    """原子写入本地配置或证书，并按用途设置最小文件权限。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(mode)
    temporary.replace(path)


def write_mode_config(mode: str, *, config_path: Path | None = None) -> Path:
    if mode not in {"host", "client"}:
        raise ValueError("运行模式必须是 host 或 client")
    path = config_root() / "mode.json"
    payload = {"format_version": 1, "mode": mode}
    if config_path is not None:
        payload["config_path"] = str(config_path.resolve())
    _write_private(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def write_host_config(host: str, port: int, data_dir: Path) -> Path:
    if host not in {"127.0.0.1", *discover_lan_addresses()}:
        raise ValueError("请选择本机检测到的明确局域网地址")
    if not 1024 <= port <= 65534:
        raise ValueError("主机端口必须在 1024—65534 之间，下一端口用于 Agent 安全通道")
    windows_system_mode = os.name == "nt" and os.getenv("PARTYOPS_ENVIRONMENT") != "test"
    resolved_data_dir = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps"
        if windows_system_mode
        else data_dir.expanduser().resolve()
    )
    values = {
        "PARTYOPS_MODE": "host",
        "PARTYOPS_ENVIRONMENT": "production",
        "PARTYOPS_HOST": host,
        "PARTYOPS_PORT": str(port),
        "PARTYOPS_AGENT_PORT": str(port + 1),
        "PARTYOPS_DATA_DIR": str(resolved_data_dir),
        "PARTYOPS_STRICT_SQLITE": "true",
        "PARTYOPS_SEED_DEMO": "false",
        "PARTYOPS_TLS_ENABLED": "true",
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
    content = "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items()) + "\n"
    path = (
        Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps" / "partyops.env"
        if windows_system_mode
        else config_root() / "partyops.env"
    )
    _write_private(path, content)
    write_mode_config("host", config_path=path)
    if windows_system_mode:
        _write_private(
            path.parent / "mode.json",
            json.dumps({"format_version": 1, "mode": "host", "config_path": str(path)}, ensure_ascii=False, indent=2),
        )
    return path


def write_client_config(
    host_url: str, token: str, backup_dir: Path, interval_seconds: int
) -> Path:
    config: dict[str, object] = {
        "mode": "client",
        "host_url": host_url.rstrip("/"),
        "pairing_token": token.strip(),
        "backup_dir": str(backup_dir.expanduser().resolve()),
        "open_browser": True,
        "interval_seconds": interval_seconds,
        "notification_interval_seconds": 30,
    }
    validate_config(config)
    if not 60 <= interval_seconds <= 86400:
        raise ValueError("灾备拉取间隔必须在 60 秒到 24 小时之间")
    path = config_root() / "client.json"
    _write_private(path, json.dumps(config, ensure_ascii=False, indent=2))
    write_mode_config("client")
    return path


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
    values: dict[str, object] = {
        "mode": "client",
        "host_url": host_url.rstrip("/"),
        "agent_url": str(enrollment.get("agent_url", "")),
        "device_id": device_id,
        "device_token": token,
        "device_name": device_name.strip(),
        "backup_dir": str(backup_dir.expanduser().resolve()),
        "receive_dir": str(
            (
                config_root() / "接收文件"
                if os.name == "nt"
                else Path.home() / "PartyOps-接收文件"
            ).resolve()
        ),
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
    if private_key and certificate and ca_certificate:
        pki_dir = config_root() / "pki"
        _write_private(pki_dir / "device.key", private_key)
        _write_private(pki_dir / "device.pem", certificate)
        _write_private(pki_dir / "ca.pem", ca_certificate, 0o644)
        values.update(
            {
                "key_file": str((pki_dir / "device.key").resolve()),
                "certificate_file": str((pki_dir / "device.pem").resolve()),
                "ca_file": str((pki_dir / "ca.pem").resolve()),
            }
        )
    validate_config(values)
    path = config_root() / "client.json"
    _write_private(path, json.dumps(values, ensure_ascii=False, indent=2))
    write_mode_config("client")
    if ca_certificate:
        install_internal_ca(config_root() / "pki" / "ca.pem")
    return path
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


def _spawn(command: list[str], log_path: Path, env: dict[str, str] | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    subprocess.Popen(  # noqa: S603 - 命令仅指向同包内固定可执行文件。
        command,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    handle.close()


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


def launch_host(config_path: Path) -> str:
    env = load_host_environment(config_path)
    host = env["PARTYOPS_HOST"]
    port = int(env["PARTYOPS_PORT"])
    install_host_autostart(config_path)
    if os.name == "nt":
        started = subprocess.run(
            ["sc.exe", "start", "PartyOpsHost"],
            check=False,
            capture_output=True,
            timeout=30,
        ).returncode == 0
        if not started:
            _spawn(
                [str(_executable("partyops"))],
                Path(env["PARTYOPS_DATA_DIR"]) / "launcher.log",
                env,
            )
    else:
        _spawn(
            [str(_executable("partyops"))],
            Path(env["PARTYOPS_DATA_DIR"]) / "launcher.log",
            env,
        )
    # 证书由主机进程首次启动时生成；在打开浏览器前完成一次图形授权，
    # 避免用户首先看到“不受信任证书”警告。
    _wait_and_install_ca(Path(env["PARTYOPS_DATA_DIR"]) / "secrets" / "pki" / "ca.pem")
    return f"http://{host}:{port}"


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
            f"Exec={desktop_quote(start_script)}",
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
    """在 Linux 桌面会话启动时恢复终端伴随进程。"""

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
            raise ValueError("协同机不能使用回环地址；请填写主机在办公局域网中的真实 IP")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and (
            address.is_loopback
            or address.is_link_local
            or not address.is_private
        ):
            raise ValueError("协同机不能使用回环地址或公网地址；请填写主机的真实局域网 IP")
    normalized = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "", "", "", "")
    ).rstrip("/")
    candidates = [normalized]
    if parsed.scheme == "http":
        candidates.append(
            urllib.parse.urlunparse(
                parsed._replace(scheme="https", path="", params="", query="", fragment="")
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
                ssl._create_unverified_context()
                if urllib.parse.urlparse(candidate).scheme == "https"
                else None
            )
            with urllib.request.urlopen(
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
            f'{"selected" if len(lan_addresses) <= 1 and index == 0 else ""}>'
            f'{html.escape(address)}'
            f'{" · 仅本机试用，不能协同" if address == "127.0.0.1" else " · 检测到的本机地址"}'
            "</option>"
        )
        for index, address in enumerate(addresses)
    )
    notice = (
        f'<div class="notice ok">{html.escape(message)}</div>' if message else ""
    )
    failure = (
        f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    )
    home = html.escape(str(Path.home()))
    host_data_dir = html.escape(
        str(Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "PartyOps")
        if os.name == "nt"
        else f"{Path.home()}/PartyOps-数据"
    )
    initial_mode = selected_mode if selected_mode in {"host", "client"} else ""
    no_lan_notice = (
        '<div class="inline-warning">检测到多个本机地址。请在协同电脑查看自己的 IP，选择前三段相同的地址（例如 192.168.3.x）；系统不会替你猜测虚拟网卡。</div>'
        if len(lan_addresses) > 1
        else "" if lan_addresses else '<div class="inline-warning">尚未检测到办公局域网地址。可以仅本机试用，但其他电脑无法加入；请先连接办公网络后刷新。</div>'
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>党建智办 · 首次配置</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}
main{{width:min(1040px,94vw);margin:4vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}
header{{padding:28px 38px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 34px/1.2 SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:8px 0 0;color:#776f66}}
.progress{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#ded4c8;border-bottom:1px solid #ded4c8}}.progress span{{padding:12px 18px;color:#756d64;background:#f2ebe1;font-size:12px}}.progress span.active{{color:#8f1f17;background:#f8e9e6;font-weight:700}}
section{{padding:28px 38px}}.role-title{{margin:0 0 5px;font:600 22px SimSun,serif}}.role-subtitle{{margin:0 0 18px;color:#776f66}}
.role-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.role-card{{width:100%;min-height:154px;margin:0;padding:22px;text-align:left;color:#282522;background:#fffdf8;border:1px solid #cfc3b6;cursor:pointer}}.role-card:hover,.role-card.active{{border-color:#b42318;box-shadow:inset 0 0 0 1px #b42318}}.role-card b,.role-card span,.role-card small{{display:block}}.role-card b{{font:600 21px SimSun,serif}}.role-card span{{margin:8px 0 12px;color:#b42318;font-weight:600}}.role-card small{{color:#776f66}}
.setup-panel{{margin:0 38px 30px;padding:28px;background:#fffdf8;border:1px solid #d8cec1}}.setup-panel[hidden]{{display:none}}.panel-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}}.panel-head h2{{margin:0;font:600 22px SimSun,serif}}.panel-head p{{margin:5px 0 0;color:#776f66}}.back{{width:auto;height:auto;margin:0;padding:4px 0;color:#8f1f17;background:transparent}}
.checklist{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 20px;padding:0;list-style:none}}.checklist li{{padding:10px 12px;color:#625c55;background:#f4ede3;border-left:2px solid #b42318;font-size:12px}}
.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}}label{{display:block;margin:14px 0 0}}label.full{{grid-column:1/-1}}label span{{display:block;margin-bottom:6px;font-size:12px;font-weight:600}}label small{{display:block;margin-top:5px;color:#857c72}}
input,select{{width:100%;height:44px;padding:0 12px;border:1px solid #cfc3b6;background:#fffdf8}}input:focus,select:focus{{outline:2px solid #b4231830;border-color:#b42318}}button.primary{{width:100%;height:48px;margin-top:22px;color:white;background:#b42318;border:0;font-weight:600;cursor:pointer}}button[disabled]{{cursor:not-allowed;opacity:.45}}
.test-row{{display:grid;grid-template-columns:1fr 1.2fr;gap:10px;align-items:end;grid-column:1/-1}}.test-button{{height:44px;margin:14px 0 0;color:#8f1f17;background:#fff8f5;border:1px solid #b42318;cursor:pointer}}.test-result{{min-height:44px;margin-top:14px;padding:10px 12px;color:#776f66;background:#f4ede3}}.test-result.ok{{color:#27613d;background:#eaf3ea}}.test-result.error{{color:#8f1f17;background:#f8e9e6}}
.inline-warning{{margin:12px 0;padding:10px 12px;color:#7a291f;background:#f8e9e6;border-left:3px solid #b42318}}
.notice{{margin:20px 38px 0;padding:12px 15px;border-left:3px solid}}.ok{{background:#eef4ed;border-color:#39724d}}.error{{background:#f8e9e7;border-color:#b42318}}
footer{{padding:18px 38px;color:#776f66;background:#f1e9de;border-top:1px solid #ddd2c5}}
@media(max-width:760px){{.role-grid,.form-grid,.test-row,.checklist{{grid-template-columns:1fr}}.progress{{grid-template-columns:1fr}}.setup-panel{{margin:0 18px 22px}}section,header{{padding-left:22px;padding-right:22px}}}}
</style></head><body><main><header><h1><b>党建</b>智办</h1><p>基层党建工作闭环协同系统 · PartyOps</p></header>
<div class="progress"><span class="active">1　选择这台电脑的角色</span><span>2　核对网络与配置</span><span>3　启动并确认连接</span></div>
{notice}{failure}
<section id="role-choice"><h2 class="role-title">第一步 · 这台电脑做什么</h2><p class="role-subtitle">一个团队通常只配置一台长期在线的主机，其他电脑都选择协同机。选错不会写入配置，可以返回重选。</p>
<div class="role-grid"><button type="button" class="role-card" data-role="host"><b>这是主机</b><span>保存全团队唯一业务数据</span><small>适合长期在线、地址稳定、由管理员维护的电脑。主机负责账号、备份、设备授权和文件中转。</small></button>
<button type="button" class="role-card" data-role="client"><b>这是协同机</b><span>加入已经配置好的主机</span><small>适合普通办公电脑。可登录业务系统、发布本机文件夹、浏览和下载已授权的团队文件。</small></button></div></section>
<section id="host-panel" class="setup-panel" data-mode-panel="host" hidden><div class="panel-head"><div><h2>配置主机</h2><p>完成后浏览器会打开主机，继续创建首位管理员账号。</p></div><button type="button" class="back">返回重选角色</button></div>
<ul class="checklist"><li>这台电脑会长期在线</li><li>已连接办公局域网</li><li>知道备份由谁负责</li></ul>{no_lan_notice}
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="host"><div class="form-grid">
<label><span>主机局域网地址</span><select id="host-bind" name="host">{options}</select><small>协同电脑必须能访问该地址；真实办公网地址已优先显示。</small></label>
<label><span>服务端口</span><input name="port" value="18765" inputmode="numeric"><small>通常保持 18765；相邻端口 18766 用于安全设备通道。</small></label>
<label class="full"><span>数据与备份目录</span><input name="data_dir" value="{host_data_dir}" {'readonly' if os.name == 'nt' else ''}><small>Windows 正式安装固定保存到受保护的 ProgramData，避免普通用户误删。</small></label></div>
<div id="loopback-warning" class="inline-warning" hidden>当前选择只能在这台电脑本机使用，其他电脑无法加入协同。若要协同，请连接办公网络并选择真实局域网地址。</div>
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
<footer>主机保存唯一业务数据库；协同机只保存自己的配置、接收文件和获授权索引。设备间文件仍经主机校验和审计，不启用匿名共享。</footer>
</main><script>
const initialMode={json.dumps(initial_mode)};
const roleChoice=document.getElementById('role-choice');
const panels={{host:document.getElementById('host-panel'),client:document.getElementById('client-panel')}};
const progressSteps=[...document.querySelectorAll('.progress span')];function setProgress(index){{progressSteps.forEach((step,key)=>step.classList.toggle('active',key===index))}}
function selectRole(role){{roleChoice.hidden=true;Object.entries(panels).forEach(([key,panel])=>panel.hidden=key!==role);setProgress(1);window.scrollTo({{top:0,behavior:'smooth'}})}}
document.querySelectorAll('[data-role]').forEach(button=>button.addEventListener('click',()=>selectRole(button.dataset.role)));
document.querySelectorAll('.back').forEach(button=>button.addEventListener('click',()=>{{Object.values(panels).forEach(panel=>panel.hidden=true);roleChoice.hidden=false;setProgress(0);window.scrollTo({{top:0,behavior:'smooth'}})}}));
const bind=document.getElementById('host-bind');const loopback=document.getElementById('loopback-warning');const hostSubmit=document.getElementById('host-submit');
function updateBindWarning(){{loopback.hidden=bind.value!=='127.0.0.1';hostSubmit.disabled=!bind.value}}bind.addEventListener('change',updateBindWarning);updateBindWarning();
const clientHost=document.getElementById('client-host-url');const testButton=document.getElementById('test-client-host');const testResult=document.getElementById('client-test-result');const clientSubmit=document.getElementById('client-submit');
let verifiedHost='';clientHost.addEventListener('input',()=>{{if(clientHost.value.trim()!==verifiedHost){{clientSubmit.disabled=true;clientSubmit.textContent='请先通过主机连接测试';testResult.className='test-result';testResult.textContent='地址已变化，请重新测试主机连接。'}}}});
testButton.addEventListener('click',async()=>{{const host=clientHost.value.trim();if(!host){{testResult.className='test-result error';testResult.textContent='请先填写主机地址。';return}}testButton.disabled=true;testResult.className='test-result';testResult.textContent='正在检查主机、端口和协议……';try{{const body=new URLSearchParams({{csrf:{json.dumps(csrf)},mode:'check_client',host_url:host}});const response=await fetch(location.href,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const result=await response.json();if(!response.ok)throw new Error(result.error||'连接失败');verifiedHost=result.host_url;clientHost.value=result.host_url;clientSubmit.disabled=false;clientSubmit.textContent='连接已验证，安全入网并启动协同机';testResult.className='test-result ok';testResult.textContent=`连接成功：PartyOps ${{result.app_version||''}} 主机正常，可以继续入网。`}}catch(error){{verifiedHost='';clientSubmit.disabled=true;clientSubmit.textContent='请先通过主机连接测试';testResult.className='test-result error';testResult.textContent=error instanceof Error?error.message:'无法连接主机'}}finally{{testButton.disabled=false}}}});
document.querySelectorAll('form').forEach(form=>form.addEventListener('submit',()=>setProgress(2)));
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
        ("zenity", ["--file-selection", "--directory", "--title=选择 PartyOps 共享文件夹"]),
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
            f"""<article><div><b>{name}</b><span>{local_path}</span><small>状态：{status} · {note or '暂无审批说明'}</small></div>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="rename"><input type="hidden" name="root_id" value="{root_id}"><input name="name" value="{name}" aria-label="共享目录名称"><button>重命名</button></form>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="remove"><input type="hidden" name="root_id" value="{root_id}"><button class="danger">移除</button></form></article>"""
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>党建智办 · 管理共享文件夹</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}main{{width:min(980px,92vw);margin:5vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}header{{padding:28px 36px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 30px SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:7px 0 0;color:#776f66}}section{{padding:24px 36px}}.notice{{margin:18px 36px 0;padding:10px 14px;border-left:3px solid}}.ok{{background:#eef4ed;border-color:#39724d}}.error{{background:#f8e9e7;border-color:#b42318}}article{{display:grid;grid-template-columns:minmax(0,1fr) 250px 64px;gap:10px;align-items:center;padding:13px 0;border-bottom:1px solid #e3d9cd}}article b,article span,article small{{display:block}}article span{{color:#625c55;overflow-wrap:anywhere}}article small{{margin-top:3px;color:#8b8177}}input{{width:100%;height:38px;padding:0 10px;border:1px solid #cfc3b6;background:#fffdf8}}button{{height:38px;padding:0 15px;color:#fff;background:#b42318;border:0;cursor:pointer}}article form{{display:flex;gap:6px}}article form button{{flex:0 0 auto}}button.danger{{background:#6f312b}}.add-grid{{display:grid;grid-template-columns:1fr 1fr auto;gap:10px;margin-top:18px}}.actions{{display:flex;gap:10px;margin-top:18px}}.actions form{{margin:0}}.muted{{color:#776f66}}@media(max-width:760px){{article,.add-grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><h1><b>党建</b>智办 · 管理共享文件夹</h1><p>可重复添加、移除、重命名、查看审批状态并立即同步；本机绝对路径不会上传到主机。</p></header>{notice}{failure}<section><h2>本机共享目录</h2>{''.join(rows) or '<p class="muted">尚未添加共享目录。</p>'}
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
            return [item for item in roots if isinstance(item, dict)] if isinstance(roots, list) else []

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
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
                value = lambda key: form.get(key, [""])[0]
                if not secrets.compare_digest(value("csrf"), csrf):
                    raise ValueError("管理页面已失效，请刷新后重试")
                action = value("action")
                message = ""
                if action == "add":
                    selected = Path(value("local_path")).expanduser() if value("local_path").strip() else _choose_system_folder()
                    if selected is None:
                        raise ValueError("未选择文件夹；也可以在输入框中手工填写完整路径")
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
                    rename_shared_root(host_url, token, config, config_path, value("root_id"), value("name"))
                    message = "共享目录名称已更新。"
                elif action == "remove":
                    remove_shared_root(host_url, token, config, config_path, value("root_id"))
                    message = "共享目录已停用并从本机配置移除，旧索引将立即隐藏。"
                elif action == "sync":
                    refresh_shared_root_statuses(host_url, token, config, config_path)
                    indexed, errors = scan_and_upload_roots(host_url, token, config, config_path)
                    message = f"立即同步完成，共登记 {indexed} 个文件或目录；{errors} 项无法读取。"
                elif action == "close":
                    message = "共享目录管理工具已关闭。"
                    shutdown.set()
                else:
                    raise ValueError("未知的共享目录操作")
                self._send(render_shared_root_manager(csrf, current_roots(), message=message))
            except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError) as exc:
                self._send(render_shared_root_manager(csrf, current_roots(), error=str(exc)), 400)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}"
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
    return 0


def run_wizard(open_browser: bool = True) -> int:
    csrf = secrets.token_urlsafe(24)
    shutdown = threading.Event()

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

        def do_GET(self) -> None:  # noqa: N802 - 标准库接口命名。
            self._send(render_page(csrf))

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
                if mode == "host":
                    path = write_host_config(
                        value("host"),
                        int(value("port")),
                        Path(value("data_dir")),
                    )
                    url = launch_host(path)
                    environment = (
                        load_host_environment(path) if path.exists() else {}
                    )
                    if environment.get("PARTYOPS_TLS_ENABLED", "").lower() == "true":
                        parsed = urllib.parse.urlparse(url)
                        url = urllib.parse.urlunparse(
                            parsed._replace(scheme="https")
                        )
                    message = f"主机已启动：{url}。首次进入后创建管理员账号。"
                elif mode == "client":
                    host_url, _health = resolve_host_url(value("host_url"))
                    device_name = value("device_name").strip()
                    if not device_name:
                        raise ValueError("必须填写本机设备名称，才能登记为协同电脑")
                    shared_text = value("shared_dir").strip()
                    backup_dir = Path(value("backup_dir")).expanduser()
                    if shared_text:
                        shared_dir = Path(shared_text).expanduser().resolve(
                            strict=True
                        )
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
                    raise ValueError("请选择主机或协同终端")
                self._send(render_page(csrf, message=message, selected_mode=mode))
                threading.Thread(
                    target=lambda: (time.sleep(1), webbrowser.open(url), shutdown.set()),
                    daemon=True,
                ).start()
            except (ValueError, OSError, urllib.error.HTTPError) as exc:
                failed_mode = locals().get("mode", "")
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
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="党建智办主机/终端配置向导")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--manage-shared-roots", action="store_true")
    parser.add_argument("--action-uri", default="")
    args = parser.parse_args()
    if args.manage_shared_roots:
        action_token = ""
        if args.action_uri:
            parsed = urllib.parse.urlparse(args.action_uri)
            if parsed.scheme != "partyops-client" or parsed.netloc != "manage-shares":
                raise SystemExit("无效的本机共享操作地址")
            action_token = parsed.path.strip("/")
            if not action_token or len(action_token) > 256:
                raise SystemExit("本机共享操作令牌无效")
        raise SystemExit(run_shared_root_manager(not args.no_browser, action_token))
    raise SystemExit(run_wizard(not args.no_browser))


if __name__ == "__main__":
    main()
