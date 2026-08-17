"""PyInstaller 首次配置向导入口。"""

from __future__ import annotations

import sys


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


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(_frozen_gui_self_test())
    from app.setup_wizard import main

    main()
