"""PyInstaller 主机入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


runtime = Path(sys.executable).resolve().parent
ocr_root = runtime / "ocr"
ocr_binary = ocr_root / "bin" / ("tesseract.exe" if os.name == "nt" else "tesseract")
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
    if "--package-self-test" in sys.argv[1:]:
        from app.package_selftest import main as selftest_main

        raise SystemExit(selftest_main(runtime))
    from app.main import run

    run()
