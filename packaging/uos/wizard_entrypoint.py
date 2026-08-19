"""PyInstaller 首次配置向导入口。"""

from __future__ import annotations

import sys
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


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(_frozen_gui_self_test())
    if sys.argv[1:] == ["--runtime-layout-self-test"]:
        raise SystemExit(_frozen_runtime_layout_self_test())
    from app.setup_wizard import main

    main()
