"""PyInstaller 主机入口。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

runtime = Path(sys.executable).resolve().parent
if sys.platform == "darwin":
    # macOS App Bundle 的可执行代码与只读资源必须分别位于 MacOS/Resources；
    # 混放会破坏 Hardened Runtime 的嵌套代码验证。
    ocr_root = runtime.parent / "Resources" / "ocr"
    ocr_binary = runtime / "tesseract"
else:
    ocr_root = runtime / "ocr"
    ocr_binary = ocr_root / "bin" / (
        "tesseract.exe" if os.name == "nt" else "tesseract"
    )
if ocr_binary.exists():
    os.environ["PATH"] = os.pathsep.join(
        part for part in (str(ocr_binary.parent), os.environ.get("PATH", "")) if part
    )
    if os.name != "nt":
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
            part
            for part in (str(ocr_root / "lib"), os.environ.get("LD_LIBRARY_PATH", ""))
            if part
        )
    os.environ["TESSDATA_PREFIX"] = str(ocr_root / "tessdata")

if __name__ == "__main__":
    if sys.argv[1:] == ["--startup-user-permission-self-test"]:
        from app.startup_selftest import run_user_permission_selftest

        try:
            print(
                json.dumps(
                    run_user_permission_selftest(runtime),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "passed": False,
                        "code": "PACKAGE_USER_RUNTIME_PERMISSION_SELFTEST_FAILED",
                        "error": str(exc)[-2000:],
                    },
                    ensure_ascii=False,
                )
            )
            raise SystemExit(3) from exc
        raise SystemExit(0)
    if sys.argv[1:] == ["--startup-self-test"]:
        from app.startup_selftest import main as startup_selftest_main

        raise SystemExit(startup_selftest_main(runtime))
    if sys.argv[1:] == ["--startup-self-test-child"]:
        from app.main import run

        run()
        raise SystemExit(0)
    if "--package-self-test" in sys.argv[1:]:
        from app.package_selftest import main as selftest_main

        raise SystemExit(selftest_main(runtime))
    from app.main import run

    run()
