"""便携版首次配置向导：选择主机或终端，不创建第二份业务数据库。"""

from __future__ import annotations

import argparse
import getpass
import html
import http.client
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
    """探测主机协议；允许从旧 HTTP 地址自动升级到 HTTPS，但绝不降级。"""

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
    headers = {"X-PartyOps-Pairing": token} if token else {}
    last_error: BaseException | None = None
    for candidate in dict.fromkeys(candidates):
        request = urllib.request.Request(
            f"{candidate}/api/v1/health",
            headers=headers,
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


def render_page(csrf: str, message: str = "", error: str = "") -> str:
    addresses = ["127.0.0.1", *discover_lan_addresses()]
    options = "".join(
        f'<option value="{html.escape(address)}">{html.escape(address)}</option>'
        for address in addresses
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
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>党建智办 · 首次配置</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#282522;background:#f7f1e7;font:14px/1.6 system-ui,"Noto Sans CJK SC",sans-serif}}
main{{width:min(980px,92vw);margin:5vh auto;background:#fbf8f1;border:1px solid #d8cec1;box-shadow:0 22px 70px #5d30221a}}
header{{padding:30px 38px;border-bottom:3px solid #b42318}}h1{{margin:0;font:600 34px/1.2 SimSun,serif}}h1 b{{color:#b42318}}header p{{margin:8px 0 0;color:#776f66}}
.grid{{display:grid;grid-template-columns:1fr 1fr}}section{{padding:34px 38px}}section+section{{border-left:1px solid #ddd2c5}}
h2{{margin:0 0 6px;font-size:20px}}.hint{{min-height:44px;color:#776f66}}label{{display:block;margin:17px 0 0}}label span{{display:block;margin-bottom:6px;font-size:12px}}
input,select{{width:100%;height:42px;padding:0 12px;border:1px solid #cfc3b6;background:#fffdf8}}button{{width:100%;height:46px;margin-top:24px;color:white;background:#b42318;border:0;font-weight:600;cursor:pointer}}
.notice{{margin:20px 38px 0;padding:12px 15px;border-left:3px solid}}.ok{{background:#eef4ed;border-color:#39724d}}.error{{background:#f8e9e7;border-color:#b42318}}
footer{{padding:18px 38px;color:#776f66;background:#f1e9de;border-top:1px solid #ddd2c5}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}section+section{{border-left:0;border-top:1px solid #ddd2c5}}}}
</style></head><body><main><header><h1><b>党建</b>智办</h1><p>基层党建工作闭环协同系统 · PartyOps</p></header>
{notice}{failure}<div class="grid">
<section><h2>配置为主机</h2><p class="hint">本机保存唯一数据库、附件和备份，并向可信局域网提供服务。</p>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="host">
<label><span>绑定地址</span><select name="host">{options}</select></label>
<label><span>服务端口</span><input name="port" value="18765" inputmode="numeric"></label>
<label><span>数据目录</span><input name="data_dir" value="{host_data_dir}" {'readonly' if os.name == 'nt' else ''}></label>
<button>保存并启动主机</button></form></section>
<section><h2>加入协同终端</h2><p class="hint">终端不创建业务数据库。使用主机管理员生成的 10 分钟入网码加入设备中心，可按授权共享本机文件夹。</p>
<form method="post"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="mode" value="client">
<label><span>主机地址</span><input name="host_url" required placeholder="https://192.168.1.20:18765"></label>
<label><span>本机设备名称</span><input name="device_name" required placeholder="例如：组织部-小王电脑"></label>
<label><span>一次性入网码</span><input name="token" required type="password" autocomplete="off"></label>
<label><span>允许共享的本机文件夹（可选）</span><input name="shared_dir" placeholder="{home}/Documents/党建资料"></label>
<label><span>灾备目录</span><input name="backup_dir" value="{home}/PartyOps-灾备副本"></label>
<button>安全入网并启动终端</button></form></section></div>
<footer>主机保存唯一业务数据库；终端只有获批的只读索引、接收文件和灾备副本。旧版配对令牌仍可用于灾备拉取，但不能访问文件协同能力。</footer>
</main></body></html>"""


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
                self._send(render_page(csrf, message=message))
                threading.Thread(
                    target=lambda: (time.sleep(1), webbrowser.open(url), shutdown.set()),
                    daemon=True,
                ).start()
            except (ValueError, OSError, urllib.error.HTTPError) as exc:
                self._send(render_page(csrf, error=str(exc)), 400)
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
