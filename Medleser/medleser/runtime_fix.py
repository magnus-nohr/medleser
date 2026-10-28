"""Load the newest Microsoft C++ runtime (msvcp140.dll) before any extension module.

pywinrt ships an older msvcp140.dll (14.29) next to its modules. Windows reuses
the first DLL loaded under a given name, so if WinRT is imported before
onnxruntime (Piper), onnxruntime ends up on the old runtime and crashes with an
access violation. Loading the newest available msvcp140.dll first – from the
application bundle or System32 – avoids that, whatever the import order is.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional

_loaded: Optional[str] = None


def file_version(path: Path) -> tuple[int, int, int, int]:
    """Return the Windows file version of a DLL (0,0,0,0 if unknown)."""
    if sys.platform != "win32":
        return (0, 0, 0, 0)
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return (0, 0, 0, 0)
    buf = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buf):
        return (0, 0, 0, 0)
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return (0, 0, 0, 0)

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
                    ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD)]

    info = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def preload_cpp_runtime() -> Optional[str]:
    """Idempotent; returns the path of the msvcp140.dll that was loaded first."""
    global _loaded
    if sys.platform != "win32" or _loaded is not None:
        return _loaded
    candidates: list[Path] = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(Path(bundle) / "msvcp140.dll")
    candidates.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "msvcp140.dll")
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None
    best = max(existing, key=file_version)
    try:
        ctypes.WinDLL(str(best))
        _loaded = str(best)
    except OSError:
        _loaded = None
    return _loaded
