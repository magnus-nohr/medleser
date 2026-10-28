"""Application context: wires storage, providers, jobs and settings together."""

from __future__ import annotations

import secrets as pysecrets
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import AppPaths, default_export_dir
from .jobs import JobManager
from .speech.generator import DEFAULT_CLOUD_LIMIT, AudioService
from .speech.providers import ProviderRegistry
from .speech.text_prep import ReadingOptions
from .storage.db import Database
from .storage.projects import ProjectStore
from .storage.secrets import SecretStore

DEFAULT_READING: dict[str, Any] = {
    "font_family": "atkinson",
    "font_size": 22,
    "line_height": 1.8,
    "letter_spacing": 0.02,
    "word_spacing": 0.08,
    "text_width": 68,
    "theme": "light",
    "custom_colors": {"background": "#FBFAF7", "text": "#1B1B1B", "highlight": "#FFE066"},
    "highlight_color": "yellow",
    "paragraph_shading": True,
    "auto_scroll": True,
    "reduce_motion": False,
    "playback_rate": 1.0,
    "shortcuts": True,
}

DEFAULT_APP: dict[str, Any] = {
    "ui_theme": "system",
    "default_language": "nb",
    "max_cloud_chars_per_document": DEFAULT_CLOUD_LIMIT,
    "export_dir": None,
    "default_options": ReadingOptions().to_dict(),
    "copyright_ack": False,
}


def _merge(defaults: dict[str, Any], stored: Any) -> dict[str, Any]:
    out = dict(defaults)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in defaults:
                out[key] = value
    return out


@dataclass
class AppContext:
    paths: AppPaths
    db: Database
    store: ProjectStore
    secrets: SecretStore
    providers: ProviderRegistry
    audio: AudioService
    jobs: JobManager
    token: str = field(default_factory=lambda: pysecrets.token_urlsafe(32))
    port: Optional[int] = None
    window: Any = None  # pywebview window when running as desktop app
    desktop: bool = False

    # -- settings ----------------------------------------------------------
    def reading_settings(self) -> dict[str, Any]:
        return _merge(DEFAULT_READING, self.db.get_setting("reading"))

    def app_settings(self) -> dict[str, Any]:
        settings = _merge(DEFAULT_APP, self.db.get_setting("app"))
        settings["default_options"] = ReadingOptions.from_dict(settings.get("default_options")).to_dict()
        return settings

    def export_dir(self):
        from pathlib import Path

        configured = self.app_settings().get("export_dir")
        return Path(configured) if configured else default_export_dir()

    def close(self) -> None:
        self.jobs.shutdown()
        self.providers.close()
        self.db.close()


def build_context(paths: AppPaths, *, secret_store: Optional[SecretStore] = None,
                  http_transport=None, workers: int = 3) -> AppContext:
    paths.ensure()
    db = Database(paths.db)
    store = ProjectStore(paths.projects, db)
    secret_store = secret_store or SecretStore()
    providers = ProviderRegistry(paths, secret_store, http_transport=http_transport)
    audio = AudioService(store, db, providers)
    return AppContext(paths=paths, db=db, store=store, secrets=secret_store, providers=providers,
                      audio=audio, jobs=JobManager(workers))
