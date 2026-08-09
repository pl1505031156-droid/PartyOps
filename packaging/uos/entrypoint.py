"""PyInstaller 主机入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


runtime = Path(sys.executable).resolve().parent
ocr_root = runtime / "ocr"
ocr_binary = ocr_root / "bin" / "tesseract"
if ocr_binary.exists():
    os.environ["PATH"] = f"{ocr_binary.parent}:{os.environ.get('PATH', '')}"
    os.environ["LD_LIBRARY_PATH"] = (
        f"{ocr_root / 'lib'}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    )
    os.environ["TESSDATA_PREFIX"] = str(ocr_root / "tessdata")

from app.main import run


if __name__ == "__main__":
    run()
