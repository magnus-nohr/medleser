"""Project folders.

Layout of one project (``<data>/projects/<id>/``)::

    project.json     settings: language, voice, reading options, playback position
    document.json    extracted document (blocks), incl. user corrections
    source/<file>    local copy of the original document
    segments/        generated audio segments (<key>.wav + <key>.json timings)
    audio/           medleser.mp3 (whole document) + sync.json (timestamps)

Never contains API keys.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..documents.model import Document
from ..errors import UserFacingError
from ..speech.audio import read_json, write_json_atomic
from .db import Database

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, default: str = "dokument") -> str:
    """Strip directories and characters Windows does not allow."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        name = default
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    stem = stem[:120]
    ext = ext[:10]
    return f"{stem}.{ext}" if ext else stem


def default_project_settings(project_id: str, title: str, source_file: str) -> dict[str, Any]:
    return {
        "format": "medleser-project",
        "version": 1,
        "id": project_id,
        "title": title,
        "source_file": source_file,
        "language": None,
        "provider": None,
        "voice_id": None,
        "model_id": None,
        "speed": 1.0,
        "options": {},
        "playback": {"position": 0.0},
        "max_cloud_chars": None,
        "audio": None,
    }


class ProjectStore:
    def __init__(self, root: Path, db: Database) -> None:
        self.root = root
        self.db = db
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)

    # -- paths --------------------------------------------------------------
    def check_id(self, project_id: str) -> str:
        if not isinstance(project_id, str) or not _ID_RE.match(project_id):
            raise UserFacingError("Fant ikke dokumentet.", code="not_found", status=404)
        return project_id

    def dir(self, project_id: str) -> Path:
        return self.root / self.check_id(project_id)

    def _subdir(self, project_id: str, name: str) -> Path:
        # Never recreate the folder of a project that has been deleted meanwhile
        # (e.g. by the user while audio was still being generated).
        pdir = self.require(project_id)
        path = pdir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def segments_dir(self, project_id: str) -> Path:
        return self._subdir(project_id, "segments")

    def audio_dir(self, project_id: str) -> Path:
        return self._subdir(project_id, "audio")

    def lock(self, project_id: str) -> threading.RLock:
        """Per-project lock for read-modify-write of project.json."""
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.RLock())

    def audio_path(self, project_id: str) -> Path:
        return self.audio_dir(project_id) / "medleser.mp3"

    def sync_path(self, project_id: str) -> Path:
        return self.audio_dir(project_id) / "sync.json"

    # -- lifecycle ----------------------------------------------------------
    def create(self, source_path: Path, original_name: str, size_bytes: int) -> str:
        project_id = uuid.uuid4().hex
        pdir = self.root / project_id
        (pdir / "source").mkdir(parents=True)
        name = safe_filename(original_name)
        shutil.copyfile(source_path, pdir / "source" / name)
        title = Path(name).stem
        write_json_atomic(pdir / "project.json", default_project_settings(project_id, title, f"source/{name}"))
        ext = Path(name).suffix.lower().lstrip(".")
        self.db.insert_project(id=project_id, title=title, source_name=name, source_type=ext,
                               size_bytes=size_bytes, status="importing")
        return project_id

    def exists(self, project_id: str) -> bool:
        try:
            return (self.dir(project_id) / "project.json").exists()
        except UserFacingError:
            return False

    def require(self, project_id: str) -> Path:
        if not self.exists(project_id):
            raise UserFacingError("Fant ikke dokumentet. Det kan ha blitt slettet.", code="not_found", status=404)
        return self.dir(project_id)

    def delete(self, project_id: str) -> None:
        pdir = self.dir(project_id).resolve()
        if pdir.parent != self.root.resolve():
            raise UserFacingError("Ugyldig prosjektmappe.", code="invalid_path")
        for attempt in range(5):
            try:
                if pdir.exists():
                    shutil.rmtree(pdir)
                break
            except PermissionError:
                # A file may still be open (e.g. audio being streamed); retry shortly.
                time.sleep(0.3 * (attempt + 1))
        else:
            raise UserFacingError(
                "Noen filer i prosjektet er i bruk og kunne ikke slettes. Lukk avspillingen og prøv igjen.",
                code="delete_failed",
            )
        self.db.delete_project(project_id)

    # -- documents and settings ----------------------------------------------
    def source_path(self, project_id: str) -> Path:
        settings = self.load_settings(project_id)
        rel = settings.get("source_file") or ""
        path = (self.dir(project_id) / rel).resolve()
        if self.dir(project_id).resolve() not in path.parents:
            raise UserFacingError("Ugyldig filreferanse i prosjektet.", code="invalid_path")
        return path

    def save_document(self, project_id: str, document: Document) -> None:
        write_json_atomic(self.dir(project_id) / "document.json", document.to_dict())

    def load_document(self, project_id: str) -> Optional[Document]:
        data = read_json(self.dir(project_id) / "document.json")
        return Document.from_dict(data) if data else None

    def load_settings(self, project_id: str) -> dict[str, Any]:
        data = read_json(self.require(project_id) / "project.json")
        if not isinstance(data, dict):
            raise UserFacingError("Prosjektfilen er skadet.", code="corrupt_project")
        return data

    def save_settings(self, project_id: str, settings: dict[str, Any]) -> None:
        write_json_atomic(self.dir(project_id) / "project.json", settings)

    def update_settings(self, project_id: str, **changes: Any) -> dict[str, Any]:
        with self.lock(project_id):
            settings = self.load_settings(project_id)
            settings.update(changes)
            self.save_settings(project_id, settings)
            return settings

    def disk_usage(self, project_id: Optional[str] = None) -> int:
        base = self.dir(project_id) if project_id else self.root
        total = 0
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return total
