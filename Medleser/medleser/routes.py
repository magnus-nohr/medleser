"""HTTP API used by the web UI (all routes are protected by server.security)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS
from .context import DEFAULT_APP, DEFAULT_READING, AppContext
from .errors import UserFacingError
from .jobs import Job
from .speech.providers import piper_catalog
from .speech.providers.base import TTSError
from .speech.text_prep import ReadingOptions, build_plan, decide_block
from .storage.projects import safe_filename

_LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

EXTERNAL_LINKS = {
    "elevenlabs_home": "https://elevenlabs.io/",
    "elevenlabs_signup": "https://elevenlabs.io/app/sign-up",
    "elevenlabs_api_keys": "https://elevenlabs.io/app/settings/api-keys",
    "elevenlabs_pricing": "https://elevenlabs.io/pricing",
    "elevenlabs_privacy": "https://elevenlabs.io/privacy-policy",
    "elevenlabs_usage": "https://elevenlabs.io/app/subscription",
    "license": "https://www.gnu.org/licenses/agpl-3.0.html",
    "piper_project": "https://github.com/OHF-Voice/piper1-gpl",
    "piper_voices": "https://huggingface.co/rhasspy/piper-voices",
    "windows_speech_settings": "ms-settings:speech",
    "windows_language_settings": "ms-settings:regionlanguage",
}
_SAMPLE_RE = re.compile(r"^sample-[0-9a-f]{32}\.(wav|mp3)$")
# pywebview only accepts letters, digits and spaces before the parenthesis (no hyphen).
MP3_FILE_TYPES = ("MP3 lydfil (*.mp3)",)


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


# ---------------------------------------------------------------------------
# App and settings


@router.get("/app")
def app_info(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)
    return {
        "version": __version__,
        "desktop": ctx.desktop,
        "data_dir": str(ctx.paths.root),
        "export_dir": str(ctx.export_dir()),
        "keyring_backend": ctx.secrets.backend_name(),
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
        "active_jobs": [j.to_dict() for j in ctx.jobs.active()],
    }


@router.get("/settings")
def get_settings(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)
    return {"reading": ctx.reading_settings(), "app": ctx.app_settings(),
            "defaults": {"reading": DEFAULT_READING, "app": DEFAULT_APP}}


@router.put("/settings/reading")
def put_reading_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    ctx = ctx_of(request)
    merged = ctx.reading_settings()
    for key, value in body.items():
        if key in DEFAULT_READING:
            merged[key] = value
    merged["font_size"] = max(12, min(64, int(merged.get("font_size", 22))))
    merged["line_height"] = max(1.0, min(3.0, float(merged.get("line_height", 1.8))))
    merged["text_width"] = max(30, min(120, int(merged.get("text_width", 68))))
    merged["playback_rate"] = max(0.5, min(2.5, float(merged.get("playback_rate", 1.0))))
    ctx.db.set_setting("reading", merged)
    return merged


@router.put("/settings/app")
def put_app_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    ctx = ctx_of(request)
    merged = ctx.app_settings()
    for key, value in body.items():
        if key in DEFAULT_APP:
            merged[key] = value
    if merged.get("default_options") is not None:
        merged["default_options"] = ReadingOptions.from_dict(merged["default_options"]).to_dict()
    limit = merged.get("max_cloud_chars_per_document")
    merged["max_cloud_chars_per_document"] = max(0, int(limit)) if limit not in (None, "") else 0
    if merged.get("export_dir"):
        merged["export_dir"] = str(Path(merged["export_dir"]))
    ctx.db.set_setting("app", merged)
    return merged


class LinkBody(BaseModel):
    target: str


@router.post("/system/open-link")
def open_link(request: Request, body: LinkBody) -> dict[str, Any]:
    url = EXTERNAL_LINKS.get(body.target)
    if not url:
        raise UserFacingError("Ukjent lenke.", code="unknown_link")
    if url.startswith("ms-settings:"):
        if sys.platform == "win32":
            os.startfile(url)  # noqa: S606 - fixed, allow-listed Windows settings URI
    else:
        webbrowser.open(url)
    return {"opened": True}


class FolderBody(BaseModel):
    target: str  # "data" | "export" | "exported"
    path: Optional[str] = None


@router.post("/system/open-folder")
def open_folder(request: Request, body: FolderBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    if body.target == "data":
        folder = ctx.paths.root
    elif body.target == "export":
        folder = ctx.export_dir()
        folder.mkdir(parents=True, exist_ok=True)
    elif body.target == "exported" and body.path:
        target = Path(body.path)
        if str(target) not in _recent_exports(ctx):
            raise UserFacingError("Ukjent fil.", code="unknown_path")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(target)])  # noqa: S603,S607
        return {"opened": True}
    else:
        raise UserFacingError("Ukjent mappe.", code="unknown_folder")
    if sys.platform == "win32":
        os.startfile(str(folder))  # noqa: S606 - our own data/export folder
    return {"opened": True}


@router.post("/system/choose-export-dir")
def choose_export_dir(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)
    if ctx.window is None:
        raise UserFacingError("Mappevalg er bare tilgjengelig i programvinduet.", code="not_desktop")
    import webview

    result = ctx.window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(ctx.export_dir()))
    if not result:
        return {"cancelled": True}
    folder = Path(result if isinstance(result, str) else result[0])
    settings = ctx.app_settings()
    settings["export_dir"] = str(folder)
    ctx.db.set_setting("app", settings)
    return {"export_dir": str(folder)}


def _recent_exports(ctx: AppContext) -> set[str]:
    if not hasattr(ctx, "_recent_exports"):
        ctx._recent_exports = set()  # type: ignore[attr-defined]
    return ctx._recent_exports  # type: ignore[attr-defined]


@router.get("/storage")
def storage_info(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)

    def size(path: Path) -> int:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0

    return {
        "data_dir": str(ctx.paths.root),
        "projects_bytes": size(ctx.paths.projects),
        "voices_bytes": size(ctx.paths.voices),
        "cache_bytes": size(ctx.paths.cache),
        "logs_bytes": size(ctx.paths.logs),
    }


@router.post("/storage/clear-cache")
def clear_cache(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)
    shutil.rmtree(ctx.paths.cache, ignore_errors=True)
    ctx.paths.cache.mkdir(parents=True, exist_ok=True)
    return {"cleared": True}


# ---------------------------------------------------------------------------
# Jobs


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict[str, Any]:
    job = ctx_of(request).jobs.get(job_id)
    if job is None:
        raise UserFacingError("Fant ikke oppgaven.", code="not_found", status=404)
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    job = ctx_of(request).jobs.cancel(job_id)
    if job is None:
        raise UserFacingError("Fant ikke oppgaven.", code="not_found", status=404)
    return job.to_dict()


# ---------------------------------------------------------------------------
# Projects


def _project_summary(ctx: AppContext, row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row.get(k) for k in ("id", "title", "source_name", "source_type", "size_bytes", "page_count",
                                   "language", "status", "audio_state", "audio_duration", "created_at",
                                   "updated_at", "opened_at")}
    out["active_jobs"] = [j.to_dict() for j in ctx.jobs.for_project(row["id"]) if j.active]
    return out


def default_voice(ctx: AppContext, language: str) -> tuple[Optional[str], Optional[str]]:
    app = ctx.app_settings()
    remembered = (ctx.db.get_setting("last_voice") or {}).get(language)
    if remembered:
        try:
            provider = ctx.providers.get(remembered.get("provider"))
            if any(v.id == remembered.get("voice_id") for v in provider.list_voices(language)):
                return remembered["provider"], remembered["voice_id"]
        except TTSError:
            pass
    for provider_id in ("piper", "windows"):
        try:
            voices = ctx.providers.get(provider_id).list_voices(language)
        except TTSError:
            continue
        if voices:
            return provider_id, voices[0].id
    del app
    return None, None


def _import_job(ctx: AppContext, project_id: str, force_ocr: bool):
    def run(job: Job) -> dict[str, Any]:
        from .documents.importer import import_document

        settings = ctx.store.load_settings(project_id)
        source = ctx.store.source_path(project_id)
        job.update(0.02, "Leser dokumentet …")
        try:
            document = import_document(
                source,
                original_name=source.name,
                force_ocr=force_ocr,
                progress=lambda f, msg: job.update(0.02 + 0.93 * f, msg),
                cancel=job.cancel_event,
            )
        except UserFacingError as exc:
            if exc.code != "cancelled":
                ctx.db.update_project(project_id, status="error")
                ctx.store.update_settings(project_id, import_error=exc.to_dict())
            raise
        ctx.store.save_document(project_id, document)
        app = ctx.app_settings()
        language = document.language if document.language not in ("und", "") else app["default_language"]
        if settings.get("language_user_set"):
            language = settings.get("language") or language
        updates: dict[str, Any] = {"title": document.title or settings.get("title"), "language": language,
                                   "import_error": None}
        if not settings.get("options"):
            updates["options"] = app["default_options"]
        if not settings.get("provider"):
            provider_id, voice_id = default_voice(ctx, language)
            updates.update(provider=provider_id, voice_id=voice_id)
        ctx.store.update_settings(project_id, **updates)
        ctx.db.update_project(project_id, title=updates["title"], page_count=document.page_count,
                              language=language, status="ready", size_bytes=document.size_bytes)
        return {"blocks": len(document.blocks), "language": document.language}

    return run


@router.post("/projects")
async def upload(request: Request, file: UploadFile = File(...), force_ocr: bool = Form(False)) -> dict[str, Any]:
    ctx = ctx_of(request)
    name = safe_filename(file.filename or "dokument")
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UserFacingError(
            "Medleser kan åpne PDF, Word (.docx), EPUB og tekstfiler (.txt). Denne filtypen støttes ikke.",
            code="unsupported_type",
        )
    tmp_dir = Path(tempfile.mkdtemp(prefix="medleser-upload-", dir=str(ctx.paths.cache)))
    tmp = tmp_dir / name
    size = 0
    try:
        with open(tmp, "wb") as fh:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UserFacingError(
                        f"Filen er for stor. Maksimal størrelse er {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                        code="too_large",
                    )
                fh.write(chunk)
        if size == 0:
            raise UserFacingError("Filen er tom.", code="empty_file")
        project_id = ctx.store.create(tmp, name, size)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    job = ctx.jobs.submit("import", _import_job(ctx, project_id, force_ocr), project_id=project_id,
                          label="Leser dokumentet")
    return {"project_id": project_id, "job": job.to_dict()}


@router.get("/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    ctx = ctx_of(request)
    return [_project_summary(ctx, row) for row in ctx.db.list_projects() if ctx.store.exists(row["id"])]


def _decisions(document, options: ReadingOptions) -> dict[str, Any]:
    return {b.id: decide_block(b, options).to_dict() for b in document.blocks}


@router.get("/projects/{project_id}")
def get_project(request: Request, project_id: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    ctx.store.require(project_id)
    row = ctx.db.get_project(project_id) or {"id": project_id}
    settings = ctx.store.load_settings(project_id)
    document = ctx.store.load_document(project_id)
    out: dict[str, Any] = {
        "project": _project_summary(ctx, row),
        "settings": settings,
        "document": document.to_dict() if document else None,
        "jobs": [j.to_dict() for j in ctx.jobs.for_project(project_id) if j.active],
    }
    if document:
        options = ReadingOptions.from_dict(settings.get("options"))
        out["decisions"] = _decisions(document, options)
        out["chapters"] = document.chapters()
        out["audio_state"] = ctx.audio.audio_state(project_id)
        language = settings.get("language") or document.language
        plan = build_plan(document, options, language or "nb")
        out["reading_summary"] = plan.to_summary()
    return out


class ProjectPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    language: Optional[str] = Field(default=None, max_length=10)
    provider: Optional[str] = None
    voice_id: Optional[str] = Field(default=None, max_length=300)
    model_id: Optional[str] = Field(default=None, max_length=100)
    speed: Optional[float] = None
    options: Optional[dict[str, Any]] = None
    max_cloud_chars: Optional[int] = None
    clear_max_cloud_chars: bool = False


@router.patch("/projects/{project_id}")
def patch_project(request: Request, project_id: str, body: ProjectPatch) -> dict[str, Any]:
    ctx = ctx_of(request)
    settings = ctx.store.load_settings(project_id)
    changes: dict[str, Any] = {}
    if body.title is not None and body.title.strip():
        changes["title"] = body.title.strip()
        ctx.db.update_project(project_id, title=changes["title"])
    if body.language is not None:
        changes["language"] = body.language
        changes["language_user_set"] = True
        ctx.db.update_project(project_id, language=body.language)
    if body.provider is not None:
        ctx.providers.get(body.provider)  # validates
        changes["provider"] = body.provider
        if body.provider != settings.get("provider") and body.voice_id is None:
            changes["voice_id"] = None
            changes["model_id"] = None
    if body.voice_id is not None:
        changes["voice_id"] = body.voice_id
    if body.model_id is not None:
        changes["model_id"] = body.model_id or None
    if body.speed is not None:
        changes["speed"] = max(0.7, min(1.5, float(body.speed)))
    if body.options is not None:
        current = settings.get("options") or {}
        merged = {**current, **body.options}
        # Per-paragraph and per-table choices are merged, so one change never undoes another.
        for key in ("block_overrides", "table_overrides"):
            if key in body.options:
                merged[key] = {**(current.get(key) or {}), **(body.options.get(key) or {})}
        changes["options"] = ReadingOptions.from_dict(merged).to_dict()
    if body.max_cloud_chars is not None:
        changes["max_cloud_chars"] = max(0, int(body.max_cloud_chars))
    if body.clear_max_cloud_chars:
        changes["max_cloud_chars"] = None
    settings = ctx.store.update_settings(project_id, **changes)
    if changes.get("voice_id") and settings.get("provider"):
        remembered = ctx.db.get_setting("last_voice") or {}
        lang = settings.get("language") or "nb"
        remembered[lang] = {"provider": settings["provider"], "voice_id": settings["voice_id"],
                            "model_id": settings.get("model_id")}
        ctx.db.set_setting("last_voice", remembered)
    return get_project(request, project_id)


class PlaybackBody(BaseModel):
    position: float


@router.put("/projects/{project_id}/playback")
def put_playback(request: Request, project_id: str, body: PlaybackBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    ctx.store.update_settings(project_id, playback={"position": max(0.0, float(body.position))})
    return {"saved": True}


@router.post("/projects/{project_id}/open")
def mark_opened(request: Request, project_id: str) -> dict[str, Any]:
    import time

    ctx = ctx_of(request)
    ctx.store.require(project_id)
    ctx.db.update_project(project_id, opened_at=time.time())
    return {"ok": True}


class BlockEdit(BaseModel):
    text: str = Field(max_length=200_000)


@router.put("/projects/{project_id}/blocks/{block_id}")
def edit_block(request: Request, project_id: str, block_id: str, body: BlockEdit) -> dict[str, Any]:
    from .documents.cleanup import detect_spans

    ctx = ctx_of(request)
    document = ctx.store.load_document(project_id)
    if document is None:
        raise UserFacingError("Dokumentet er ikke ferdig innlest.", code="not_ready", status=409)
    block = document.block_by_id(block_id)
    if block is None or block.kind == "table":
        raise UserFacingError("Fant ikke avsnittet.", code="not_found", status=404)
    new_text = body.text.replace("\r\n", "\n").strip()
    if new_text != block.text:
        if block.original_text is None:
            block.original_text = block.text
            block.original_spans = list(block.spans)
        if new_text == block.original_text:
            # Back to the extracted text: restore the importer's spans (incl. footnote markers).
            block.text = block.original_text
            block.spans = list(block.original_spans or detect_spans(block.text))
            block.original_text = None
            block.original_spans = None
        else:
            block.text = new_text
            block.spans = detect_spans(new_text)
        ctx.store.save_document(project_id, document)
    options = ReadingOptions.from_dict(ctx.store.load_settings(project_id).get("options"))
    return {"block": block.to_dict(), "decision": decide_block(block, options).to_dict()}


@router.post("/projects/{project_id}/blocks/{block_id}/restore")
def restore_block(request: Request, project_id: str, block_id: str) -> dict[str, Any]:
    from .documents.cleanup import detect_spans

    ctx = ctx_of(request)
    document = ctx.store.load_document(project_id)
    block = document.block_by_id(block_id) if document else None
    if block is None:
        raise UserFacingError("Fant ikke avsnittet.", code="not_found", status=404)
    if block.original_text is not None:
        block.text = block.original_text
        block.spans = list(block.original_spans) if block.original_spans is not None else detect_spans(block.text)
        block.original_text = None
        block.original_spans = None
        ctx.store.save_document(project_id, document)
    options = ReadingOptions.from_dict(ctx.store.load_settings(project_id).get("options"))
    return {"block": block.to_dict(), "decision": decide_block(block, options).to_dict()}


class ReimportBody(BaseModel):
    force_ocr: bool = False


@router.post("/projects/{project_id}/reimport")
def reimport(request: Request, project_id: str, body: ReimportBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    ctx.store.require(project_id)
    ctx.db.update_project(project_id, status="importing")
    job = ctx.jobs.submit("import", _import_job(ctx, project_id, body.force_ocr), project_id=project_id,
                          label="Leser dokumentet på nytt")
    return {"job": job.to_dict()}


@router.delete("/projects/{project_id}")
def delete_project(request: Request, project_id: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    ctx.store.require(project_id)
    ctx.jobs.cancel_project(project_id)
    for _ in range(50):
        if not any(j.active for j in ctx.jobs.for_project(project_id)):
            break
        threading.Event().wait(0.1)
    ctx.store.delete(project_id)
    return {"deleted": True}


# -- audio ------------------------------------------------------------------


@router.get("/projects/{project_id}/estimate")
def estimate(request: Request, project_id: str) -> dict[str, Any]:
    return ctx_of(request).audio.estimate(project_id)


class GenerateBody(BaseModel):
    confirmation_token: Optional[str] = None


@router.post("/projects/{project_id}/generate")
def generate(request: Request, project_id: str, body: GenerateBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    ctx.store.require(project_id)
    # Validate the paid-service confirmation before queueing anything.
    est = ctx.audio.estimate(project_id)
    if est["provider"]["is_paid"] and est["segments_pending"] > 0:
        if body.confirmation_token != est["confirmation_token"]:
            raise UserFacingError(
                "Du må godkjenne overslaget før teksten sendes til en betalt tjeneste.",
                code="confirmation_required", status=409,
            )
        if est["exceeds_limit"]:
            raise UserFacingError(
                "Dette ville overskride forbruksgrensen for dokumentet. Øk grensen først.",
                code="spending_limit",
            )
    ctx.db.update_project(project_id, audio_state="generating")

    def run(job: Job) -> dict[str, Any]:
        try:
            return ctx.audio.generate(job, project_id, body.confirmation_token)
        finally:
            state = ctx.audio.audio_state(project_id)
            ctx.db.update_project(project_id, audio_state="ready" if state["ready"] and not state["stale"]
                                  else ("partial" if state["cached_segments"] else "none"))

    job = ctx.jobs.submit("generate", run, project_id=project_id, label="Lager lyd")
    return {"job": job.to_dict()}


@router.get("/projects/{project_id}/audio")
def get_audio(request: Request, project_id: str):
    ctx = ctx_of(request)
    path = ctx.store.audio_path(project_id)
    if not path.exists():
        raise UserFacingError("Lyden er ikke laget ennå.", code="audio_missing", status=404)
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})


@router.get("/projects/{project_id}/sync")
def get_sync(request: Request, project_id: str):
    ctx = ctx_of(request)
    path = ctx.store.sync_path(project_id)
    if not path.exists():
        raise UserFacingError("Lyden er ikke laget ennå.", code="audio_missing", status=404)
    return FileResponse(path, media_type="application/json")


class ExportBody(BaseModel):
    mode: str = "single"  # single | chapters


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path


@router.post("/projects/{project_id}/export")
def export(request: Request, project_id: str, body: ExportBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    audio_path = ctx.store.audio_path(project_id)
    if not audio_path.exists():
        raise UserFacingError("Lag lyden først.", code="audio_missing", status=409)
    settings = ctx.store.load_settings(project_id)
    title = settings.get("title") or "Medleser"
    export_dir = ctx.export_dir()
    export_dir.mkdir(parents=True, exist_ok=True)

    if body.mode == "single":
        suggested = safe_filename(f"{title}.mp3")
        if ctx.window is not None:
            import webview

            result = ctx.window.create_file_dialog(webview.FileDialog.SAVE, directory=str(export_dir),
                                                   save_filename=suggested, file_types=MP3_FILE_TYPES)
            if not result:
                return {"cancelled": True}
            dest = Path(result if isinstance(result, str) else result[0])
            if dest.suffix.lower() != ".mp3":
                dest = dest.with_suffix(".mp3")
        else:
            dest = _unique(export_dir / suggested)
        shutil.copyfile(audio_path, dest)
        _recent_exports(ctx).add(str(dest))
        return {"saved": [str(dest)]}

    if body.mode != "chapters":
        raise UserFacingError("Ukjent lagringsvalg.", code="invalid_request")
    if ctx.window is not None:
        import webview

        result = ctx.window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(export_dir))
        if not result:
            return {"cancelled": True}
        base = Path(result if isinstance(result, str) else result[0])
    else:
        base = export_dir
    dest_dir = _unique(base / safe_filename(title, "Medleser"))

    def run(job: Job) -> dict[str, Any]:
        files = ctx.audio.export_chapters(project_id, dest_dir, job)
        for f in files:
            _recent_exports(ctx).add(str(f))
        return {"saved": [str(f) for f in files], "folder": str(dest_dir)}

    job = ctx.jobs.submit("export", run, project_id=project_id, label="Lagrer kapitler",
                          dedupe_key=f"export:{project_id}")
    return {"job": job.to_dict()}


# ---------------------------------------------------------------------------
# Voices and providers


def _provider_info(ctx: AppContext, provider) -> dict[str, Any]:
    caps = provider.get_provider_capabilities()
    status = provider.status()
    info = {"capabilities": caps.to_dict(), "status": status.to_dict()}
    if caps.auth_method == "api_key":
        info["connected"] = ctx.secrets.has(provider.id)
    return info


@router.get("/providers")
def providers(request: Request) -> list[dict[str, Any]]:
    ctx = ctx_of(request)
    return [_provider_info(ctx, p) for p in ctx.providers.all()]


@router.get("/providers/{provider_id}/voices")
def provider_voices(request: Request, provider_id: str, language: Optional[str] = None) -> dict[str, Any]:
    ctx = ctx_of(request)
    provider = ctx.providers.get(provider_id)
    voices = provider.list_voices(language)
    return {"voices": [v.to_dict() for v in voices],
            "models": [m.to_dict() for m in provider.list_models(language)]}


@router.post("/providers/{provider_id}/check")
def provider_check(request: Request, provider_id: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    provider = ctx.providers.get(provider_id)
    status = provider.check_connection()
    return {**_provider_info(ctx, provider), "status": status.to_dict()}


class KeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=300)


@router.post("/providers/{provider_id}/key")
def set_key(request: Request, provider_id: str, body: KeyBody) -> dict[str, Any]:
    from .storage.secrets import SecretStoreError

    ctx = ctx_of(request)
    provider = ctx.providers.get(provider_id)
    if provider.get_provider_capabilities().auth_method != "api_key":
        raise UserFacingError("Denne tjenesten trenger ingen nøkkel.", code="invalid_request")
    key = body.api_key.strip()
    if any(ch.isspace() for ch in key) or not key.isprintable():
        raise UserFacingError("Nøkkelen ser ikke riktig ut. Kopier den på nytt fra nettsiden.", code="invalid_key")
    try:
        ctx.secrets.set(provider_id, key)
    except SecretStoreError as exc:
        raise UserFacingError("Nøkkelen kunne ikke lagres sikkert i Windows.", code="keyring_failed",
                              details=str(exc)) from exc
    status = provider.check_connection()
    if status.state == "error" and status.account.get("error_kind") == "auth":
        # A key the service rejects is not kept.
        ctx.secrets.delete(provider_id)
    return {**_provider_info(ctx, provider), "status": status.to_dict()}


@router.delete("/providers/{provider_id}/key")
def delete_key(request: Request, provider_id: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    provider = ctx.providers.get(provider_id)
    ctx.secrets.delete(provider_id)
    if hasattr(provider, "forget"):
        provider.forget()
    return _provider_info(ctx, provider)


@router.get("/piper/catalog")
def piper_catalog_list(request: Request) -> list[dict[str, Any]]:
    ctx = ctx_of(request)
    out = []
    for entry in piper_catalog.CATALOG:
        item = entry.to_dict()
        item["installed"] = piper_catalog.is_installed(ctx.paths.piper_voices, entry.key)
        manifest = piper_catalog.read_manifest(ctx.paths.piper_voices, entry.key) or {}
        item["word_timing"] = bool(manifest.get("aligned"))
        item["job"] = next((j.to_dict() for j in ctx.jobs.active() if j.dedupe == f"voice:{entry.key}"), None)
        out.append(item)
    return out


@router.post("/piper/voices/{key}/install")
def piper_install(request: Request, key: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    if key not in piper_catalog.CATALOG_BY_KEY:
        raise UserFacingError("Ukjent stemme.", code="unknown_voice", status=404)

    def run(job: Job) -> dict[str, Any]:
        piper_catalog.install_voice(ctx.paths.piper_voices, key, progress=job.update, cancel=job.cancel_event)
        return {"key": key}

    job = ctx.jobs.submit("voice_download", run, label="Laster ned stemme", dedupe_key=f"voice:{key}")
    return {"job": job.to_dict()}


@router.delete("/piper/voices/{key}")
def piper_remove(request: Request, key: str) -> dict[str, Any]:
    ctx = ctx_of(request)
    provider = ctx.providers.get("piper")
    provider.close()
    piper_catalog.remove_voice(ctx.paths.piper_voices, key)
    return {"removed": True}


class SampleBody(BaseModel):
    provider: str
    voice_id: str = Field(max_length=300)
    language: str = "nb"
    model_id: Optional[str] = None
    project_id: Optional[str] = None
    use_document_text: bool = False
    confirm_paid: bool = False


@router.post("/samples")
def make_sample(request: Request, body: SampleBody) -> dict[str, Any]:
    ctx = ctx_of(request)
    provider = ctx.providers.get(body.provider)
    caps = provider.get_provider_capabilities()
    text = None
    if body.use_document_text and body.project_id:
        document = ctx.store.load_document(body.project_id)
        if document:
            settings = ctx.store.load_settings(body.project_id)
            plan = build_plan(document, ReadingOptions.from_dict(settings.get("options")), body.language)
            excerpt = ""
            for seg in plan.segments:
                excerpt = (excerpt + " " + seg.text).strip()
                if len(excerpt) > 160:
                    break
            text = excerpt[:300] or None
    from .speech.generator import SAMPLE_TEXTS

    sample_text = text or SAMPLE_TEXTS.get(body.language.split("-")[0], SAMPLE_TEXTS["nb"])
    if caps.is_paid and not body.confirm_paid:
        usage = provider.estimate_usage(len(sample_text), body.model_id)
        return JSONResponse(status_code=409, content={"error": {
            "code": "confirmation_required",
            "message": "En stemmeprøve med din egen tekst bruker kreditter hos tjenesten.",
            "details": None}, "characters": len(sample_text), "usage": usage.to_dict()})
    path, generated = ctx.audio.sample(ctx.paths.cache / "samples", body.provider, body.voice_id, body.language,
                                       text=sample_text, model_id=body.model_id)
    if caps.is_paid and generated:
        ctx.db.add_usage(body.project_id, provider.id, len(sample_text), body.voice_id, body.model_id)
    return {"url": f"/api/samples/{path.name}"}


@router.get("/samples/{name}")
def get_sample(request: Request, name: str):
    if not _SAMPLE_RE.match(name):
        raise UserFacingError("Ugyldig filnavn.", code="invalid_request", status=404)
    path = ctx_of(request).paths.cache / "samples" / name
    if not path.exists():
        raise UserFacingError("Fant ikke stemmeprøven.", code="not_found", status=404)
    media = "audio/wav" if name.endswith(".wav") else "audio/mpeg"
    return FileResponse(path, media_type=media)


@router.get("/providers/{provider_id}/preview/{voice_id}")
def voice_preview(request: Request, provider_id: str, voice_id: str):
    """Free, pre-recorded preview published by the provider (no credits used)."""
    ctx = ctx_of(request)
    provider = ctx.providers.get(provider_id)
    if not hasattr(provider, "preview_audio"):
        raise UserFacingError("Denne tjenesten har ingen ferdige stemmeprøver.", code="no_preview", status=404)
    path = provider.preview_audio(voice_id, ctx.paths.cache / "previews")
    return FileResponse(path, media_type="audio/mpeg")
