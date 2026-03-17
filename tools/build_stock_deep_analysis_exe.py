#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ENTRY = HERE / "stock_deep_analysis_ui.py"
OUT_DIR = HERE / "dist"
EXE_NAME = "StockDeepAnalysisUI"
PYINSTALLER_MODULE = "PyInstaller"


def ensure_pyinstaller() -> None:
    result = subprocess.run(
        [sys.executable, "-m", PYINSTALLER_MODULE, "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def build() -> None:
    ensure_pyinstaller()
    temp_root = Path(os.environ.get("TEMP", "C:/Temp"))
    cmd = [
        sys.executable,
        "-m",
        PYINSTALLER_MODULE,
        "--onefile",
        "--windowed",
        "--name",
        EXE_NAME,
        "--distpath",
        str(OUT_DIR),
        "--workpath",
        str(temp_root / "pyinstaller_stock_deep_build"),
        "--specpath",
        str(temp_root / "pyinstaller_stock_deep_spec"),
        "--hidden-import=tkinter",
        "--hidden-import=tkinter.ttk",
        "--hidden-import=tkinter.scrolledtext",
        str(ENTRY),
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    print(OUT_DIR / f"{EXE_NAME}.exe")


if __name__ == "__main__":
    build()
