"""Application paths and constants.

All user data lives under one data directory (default
``%LOCALAPPDATA%\\Medleser``). Tests and portable setups can override it
with the ``MEDLESER_DATA_DIR`` environment variable.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Upload limits (security: refuse absurdly large files early).
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
# Maximum total uncompressed size we accept inside DOCX/EPUB containers (zip-bomb guard).
MAX_UNCOMPRESSED_BYTES = 600 * 1024 * 1024
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".epub", ".txt")


def resource_dir() -> Path:
    """Directory with bundled read-only resources (web UI, fonts, icons).

    Works both from source and inside a PyInstaller bundle.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    @property
    def voices(self) -> Path:
        return self.root / "voices"

    @property
    def piper_voices(self) -> Path:
        return self.voices / "piper"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def db(self) -> Path:
        return self.root / "medleser.db"

    def ensure(self) -> "AppPaths":
        for path in (self.root, self.projects, self.piper_voices, self.logs, self.cache):
            path.mkdir(parents=True, exist_ok=True)
        return self

    @staticmethod
    def from_env() -> "AppPaths":
        override = os.environ.get("MEDLESER_DATA_DIR")
        if override:
            return AppPaths(Path(override))
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return AppPaths(Path(base) / "Medleser")


def default_export_dir() -> Path:
    """Suggested folder for exported MP3 files: Documents\\Medleser."""
    docs = Path.home() / "Documents"
    try:
        # Respect a redirected «Dokumenter» folder (OneDrive etc.).
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        # CSIDL_PERSONAL = 5
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            docs = Path(buf.value)
    except Exception:  # noqa: BLE001 - non-Windows or API failure: keep default
        pass
    return docs / "Medleser"
