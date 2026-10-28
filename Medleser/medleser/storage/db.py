"""SQLite database: project index, settings and usage log.

Large structured data (document blocks, timings) lives as JSON files in the
project folder; SQLite holds what we list, sort and sum. API keys are never
stored here (see ``secrets.py``).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_name TEXT,
    source_type TEXT,
    size_bytes INTEGER DEFAULT 0,
    page_count INTEGER,
    language TEXT,
    status TEXT NOT NULL DEFAULT 'importing',
    audio_state TEXT NOT NULL DEFAULT 'none',
    audio_duration REAL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    opened_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    provider TEXT NOT NULL,
    voice_id TEXT,
    model_id TEXT,
    characters INTEGER NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_project ON usage(project_id, provider);
"""

PROJECT_FIELDS = (
    "id", "title", "source_name", "source_type", "size_bytes", "page_count", "language",
    "status", "audio_state", "audio_duration", "created_at", "updated_at", "opened_at",
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- projects -----------------------------------------------------------
    def insert_project(self, **fields: Any) -> None:
        now = time.time()
        fields.setdefault("created_at", now)
        fields.setdefault("updated_at", now)
        fields.setdefault("opened_at", now)
        cols = [c for c in PROJECT_FIELDS if c in fields]
        with self._lock:
            self._conn.execute(
                f"INSERT INTO projects ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [fields[c] for c in cols],
            )

    def update_project(self, project_id: str, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in PROJECT_FIELDS and k != "id"}
        if not fields:
            return
        fields.setdefault("updated_at", time.time())
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE projects SET {sets} WHERE id = ?", [*fields.values(), project_id])

    def get_project(self, project_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY COALESCE(opened_at, updated_at) DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            self._conn.execute("DELETE FROM usage WHERE project_id = ?", (project_id,))

    # -- settings -------------------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    # -- usage ----------------------------------------------------------------
    def add_usage(self, project_id: Optional[str], provider: str, characters: int,
                  voice_id: Optional[str] = None, model_id: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage (project_id, provider, voice_id, model_id, characters, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, provider, voice_id, model_id, int(characters), time.time()),
            )

    def usage_for_project(self, project_id: str, provider: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(characters), 0) AS n FROM usage WHERE project_id = ? AND provider = ?",
                (project_id, provider),
            ).fetchone()
        return int(row["n"])

    def usage_total(self, provider: str, since: float = 0.0) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(characters), 0) AS n FROM usage WHERE provider = ? AND created_at >= ?",
                (provider, since),
            ).fetchone()
        return int(row["n"])
