# -*- mode: python ; coding: utf-8 -*-
# Single-file Windows executable without a console window:
#     uv run --with pyinstaller pyinstaller packaging/logscope_onefile.spec --noconfirm --clean
# (or run packaging\build_exe_onefile.ps1).  Result: dist\LogScope.exe
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
PKG = ROOT / "packaging"

# sub-modules are collected explicitly so plugins in <settings folder>\plugins can import logscope.*
hidden = collect_submodules("logscope") + ["PySide6.QtSvg", "numpy"]

# Qt parts LogScope never uses: keeps the file much smaller
unused_qt = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtNetworkAuth", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSensors", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation", "PySide6.QtPositioning",
]

a = Analysis(
    [str(PKG / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "unittest.mock"] + unused_qt,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="LogScope",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                  # <- no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PKG / "logscope.ico"),
    version=str(PKG / "version_info.txt"),
)