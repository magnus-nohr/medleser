"""Background jobs with progress and cancellation (import, downloads, audio)."""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .errors import CancelledError, UserFacingError

_LOG = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    kind: str
    project_id: Optional[str]
    label: str = ""
    state: str = "queued"  # queued | running | done | failed | cancelled
    progress: float = 0.0
    message: str = ""
    error: Optional[dict[str, Any]] = None
    result: Optional[dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    dedupe: Optional[str] = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def update(self, progress: Optional[float] = None, message: Optional[str] = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self.message = message
        self.updated_at = time.time()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError()

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "project_id": self.project_id,
            "label": self.label,
            "state": self.state,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobManager:
    def __init__(self, workers: int = 3) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="medleser-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, kind: str, fn: Callable[[Job], Optional[dict[str, Any]]], *,
               project_id: Optional[str] = None, label: str = "", dedupe_key: Optional[str] = None) -> Job:
        with self._lock:
            if self._closed:
                raise UserFacingError("Medleser avsluttes.", code="shutting_down", status=503)
            key = dedupe_key or (f"{kind}:{project_id}" if project_id else None)
            if key:
                for job in self._jobs.values():
                    if job.active and job.dedupe == key:
                        return job
            job = Job(id=uuid.uuid4().hex, kind=kind, project_id=project_id, label=label, dedupe=key)
            self._jobs[job.id] = job
            self._prune_locked()
        self._pool.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: Callable[[Job], Optional[dict[str, Any]]]) -> None:
        job.state = "running"
        job.update()
        try:
            job.check_cancel()
            result = fn(job)
            job.result = result or {}
            job.state = "done"
            job.update(progress=1.0)
        except CancelledError as exc:
            job.state = "cancelled"
            job.update(message=exc.message if job.message == "" else job.message)
            job.error = exc.to_dict()
        except UserFacingError as exc:
            job.state = "failed"
            job.error = exc.to_dict()
            job.update(message=exc.message)
            _LOG.warning("Job %s (%s) failed: %s | %s", job.id, job.kind, exc.code, exc.details)
        except Exception as exc:  # noqa: BLE001
            job.state = "failed"
            job.error = {
                "code": "internal",
                "message": "Noe gikk galt. Prøv igjen. Hvis feilen gjentar seg, start Medleser på nytt.",
                "details": "".join(traceback.format_exception_only(type(exc), exc)).strip(),
            }
            job.update(message=job.error["message"])
            _LOG.exception("Job %s (%s) crashed", job.id, job.kind)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if job and job.active:
            job.cancel_event.set()
            job.update(message="Avbryter …")
        return job

    def for_project(self, project_id: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.project_id == project_id]

    def active(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.active]

    def cancel_project(self, project_id: str) -> None:
        for job in self.for_project(project_id):
            if job.active:
                job.cancel_event.set()

    def _prune_locked(self) -> None:
        if len(self._jobs) < 200:
            return
        finished = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.updated_at)
        for job in finished[: len(self._jobs) - 150]:
            self._jobs.pop(job.id, None)

    def shutdown(self, wait: float = 3.0) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel_event.set()
        self._pool.shutdown(wait=False, cancel_futures=True)
        deadline = time.time() + wait
        while time.time() < deadline and any(j.active for j in jobs):
            time.sleep(0.05)
