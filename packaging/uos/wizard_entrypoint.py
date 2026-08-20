"""PyInstaller 首次配置向导入口。"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _frozen_gui_self_test() -> int:
    """在不写配置、不启动服务的前提下核验冻结 GUI 运行时。"""

    import tkinter

    root = tkinter.Tk()
    try:
        root.withdraw()
        root.update_idletasks()
        tcl_version = root.tk.call("info", "patchlevel")
        print(f"PartyOps 配置向导图形运行时正常（Tcl/Tk {tcl_version}）。")
    finally:
        root.destroy()
    return 0


def _frozen_runtime_layout_self_test() -> int:
    """阻断会把共享库以可执行权限解压到临时目录的单文件布局。"""

    if not getattr(sys, "frozen", False):
        print("[PACKAGE_RUNTIME_LAYOUT_INVALID] 当前不是冻结运行时。", file=sys.stderr)
        return 2
    executable_root = Path(sys.executable).resolve().parent
    runtime_root = Path(getattr(sys, "_MEIPASS", "")).resolve()
    expected_root = (executable_root / "_internal").resolve()
    if runtime_root != expected_root:
        print(
            "[PACKAGE_RUNTIME_LAYOUT_INVALID] 配置向导仍会从临时目录解包运行库。",
            file=sys.stderr,
        )
        return 2
    executable_libraries = [
        path
        for path in runtime_root.rglob("*.so*")
        if path.is_file() and path.stat().st_mode & 0o111
    ]
    if executable_libraries:
        print(
            "[PACKAGE_LIBRARY_MODE_UNSAFE] 共享库被错误标记为可执行文件："
            f"{executable_libraries[0]}",
            file=sys.stderr,
        )
        return 2
    print("PartyOps 配置向导共享运行时布局与权限正常。")
    return 0


def _frozen_desktop_server_self_test() -> int:
    """真实绑定回环端口并发布页面标记，覆盖桌面启动器必经链路。"""

    root_text = os.getenv("PARTYOPS_DESKTOP_SELFTEST_ROOT", "").strip()
    if not root_text:
        print("[PACKAGE_WIZARD_SELFTEST_ROOT_MISSING] 未提供向导自检目录。", file=sys.stderr)
        return 2
    root = Path(root_text).resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "wizard.url"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - 标准库回调名固定。
            body = b"partyops-wizard-ready"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        marker.write_text(url + "\n", encoding="utf-8")
        if os.name != "nt":
            marker.chmod(0o600)
        thread.start()
        with urllib.request.urlopen(url, timeout=5) as response:  # nosec B310 - 固定回环自检地址。
            body = response.read()
        if response.status != 200 or body != b"partyops-wizard-ready":
            raise RuntimeError("回环页面响应不完整")
        if marker.read_text(encoding="utf-8").strip() != url:
            raise RuntimeError("桌面启动标记回读不一致")
    except (OSError, RuntimeError) as exc:
        print(f"[PACKAGE_WIZARD_SERVER_INVALID] 配置向导页面自检失败：{exc}", file=sys.stderr)
        return 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        marker.unlink(missing_ok=True)
    print("PartyOps 配置向导回环页面与桌面标记正常。")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(_frozen_gui_self_test())
    if sys.argv[1:] == ["--runtime-layout-self-test"]:
        raise SystemExit(_frozen_runtime_layout_self_test())
    if sys.argv[1:] == ["--desktop-server-self-test"]:
        raise SystemExit(_frozen_desktop_server_self_test())
    from app.setup_wizard import main

    main()
