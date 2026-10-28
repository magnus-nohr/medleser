# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Medleser (one-folder build, no console window).
# Build with: scripts\build_windows.ps1   (or: pyinstaller packaging\medleser.spec --noconfirm)

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "THIRD_PARTY_LICENSES.md"), "."),
]
# Piper: espeak-ng phoneme data (and small json tables); never the training code.
datas += collect_data_files("piper", excludes=["train/**", "**/__pycache__/**"])
# keyring discovers its Windows backend through package metadata (entry points).
datas += copy_metadata("keyring")

hiddenimports = []
hiddenimports += collect_submodules("medleser")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "keyring.backends.Windows",
    "win32ctypes.core",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.globalization",
    "winrt.windows.graphics.imaging",
    "winrt.windows.media.core",
    "winrt.windows.media.ocr",
    "winrt.windows.media.speechsynthesis",
    "winrt.windows.storage.streams",
    "onnx",
    "lameenc",
    "miniaudio",
]

# The newest Microsoft C++ runtime goes to the bundle root and is loaded first
# (see medleser/runtime_fix.py). pywinrt's own, older copy is removed below.
binaries = []
system_msvcp = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "msvcp140.dll"
if system_msvcp.exists():
    binaries.append((str(system_msvcp), "."))

a = Analysis(
    [str(ROOT / "scripts" / "medleser_launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "pytest", "_pytest", "PyInstaller", "IPython", "matplotlib",
        "piper.train", "piper.http_server", "torch", "lightning", "pytorch_lightning",
        "flask", "pygments",
    ],
    noarchive=False,
)


def _keep(entry):
    dest = entry[0].replace("\\", "/").lower()
    return not dest.endswith("winrt/msvcp140.dll")


a.binaries = [b for b in a.binaries if _keep(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Medleser",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "assets" / "medleser.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Medleser",
)
