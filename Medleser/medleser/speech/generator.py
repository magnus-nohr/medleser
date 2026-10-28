"""Audio generation: reading plan -> cached segments -> MP3 + sync manifest.

* Segments are cached on disk under a key derived from provider, voice,
  model, language, speed and the exact segment text. Re-running a job only
  generates what is missing, so interrupted jobs resume and paid segments are
  never bought twice.
* Paid providers require a confirmation token from :meth:`estimate`; the
  token is bound to the exact set of segments that will be sent, so nothing
  is sent that the user has not seen and approved.
* The final MP3 is streamed segment by segment through one encoder, and the
  word timings of every segment are shifted by the exact number of samples
  written before it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..documents.model import Document
from ..errors import CancelledError, UserFacingError
from ..jobs import Job
from ..storage.db import Database
from ..storage.projects import ProjectStore
from .audio import apply_fades, read_json, read_wav, resample, silence, write_json_atomic, write_wav
from .mp3 import Mp3Tags, Mp3Writer, lame_delay_seconds, probe_mp3
from .providers import ProviderRegistry
from .providers.base import SynthesisRequest, SynthesisResult, TTSError, TTSProvider, Voice
from .text_prep import ReadingOptions, ReadingPlan, build_plan, segment_cache_key

_LOG = logging.getLogger(__name__)

DEFAULT_CLOUD_LIMIT = 50_000  # characters per document unless the user changes it
SAMPLE_TEXTS = {
    "nb": "Hei! Jeg er en av stemmene i Medleser. Slik høres jeg ut når jeg leser pensum for deg.",
    "nn": "Hei! Eg er ei av stemmene i Medleser. Slik høyrest eg ut når eg les pensum for deg.",
    "en": "Hello! I am one of the voices in Medleser. This is how I sound when I read your course texts.",
    "sv": "Hej! Jag är en av rösterna i Medleser. Så här låter jag när jag läser kurslitteratur för dig.",
    "da": "Hej! Jeg er en af stemmerne i Medleser. Sådan lyder jeg, når jeg læser pensum for dig.",
}


@dataclass
class VoiceSelection:
    provider_id: str
    voice_id: str
    model_id: Optional[str]
    language: str
    speed: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "language": self.language,
            "speed": self.speed,
        }


class AudioService:
    def __init__(self, store: ProjectStore, db: Database, providers: ProviderRegistry) -> None:
        self.store = store
        self.db = db
        self.providers = providers

    # -- planning -------------------------------------------------------------
    def selection(self, settings: dict[str, Any], document: Document) -> VoiceSelection:
        provider_id = settings.get("provider")
        voice_id = settings.get("voice_id")
        if not provider_id or not voice_id:
            raise UserFacingError("Velg en stemme før du lager lyd.", code="no_voice")
        language = settings.get("language") or document.language or "nb"
        if language == "und":
            language = "nb"
        return VoiceSelection(
            provider_id=provider_id,
            voice_id=voice_id,
            model_id=settings.get("model_id"),
            language=language,
            speed=float(settings.get("speed") or 1.0),
        )

    def plan(self, project_id: str) -> tuple[Document, dict[str, Any], ReadingPlan, VoiceSelection, TTSProvider]:
        document = self.store.load_document(project_id)
        if document is None:
            raise UserFacingError("Dokumentet er ikke ferdig innlest ennå.", code="not_ready", status=409)
        settings = self.store.load_settings(project_id)
        selection = self.selection(settings, document)
        provider = self.providers.get(selection.provider_id)
        caps = provider.get_provider_capabilities()
        plan = build_plan(
            document,
            ReadingOptions.from_dict(settings.get("options")),
            selection.language,
            max_chars=caps.preferred_segment_chars,
            hard_max=provider.max_chars(selection.model_id),
        )
        return document, settings, plan, selection, provider

    def keys(self, plan: ReadingPlan, sel: VoiceSelection) -> list[str]:
        return [
            segment_cache_key(seg.text, sel.provider_id, sel.voice_id, sel.model_id, sel.language, sel.speed)
            for seg in plan.segments
        ]

    def _segment_paths(self, project_id: str, key: str) -> tuple[Path, Path]:
        base = self.store.segments_dir(project_id)
        return base / f"{key}.wav", base / f"{key}.json"

    def is_cached(self, project_id: str, key: str) -> bool:
        wav, meta = self._segment_paths(project_id, key)
        return wav.exists() and meta.exists()

    @staticmethod
    def plan_hash(keys: list[str]) -> str:
        return hashlib.sha256("|".join(keys).encode()).hexdigest()[:24]

    @staticmethod
    def _token(project_id: str, sel: VoiceSelection, pending_keys: list[str]) -> str:
        payload = json.dumps([project_id, sel.to_dict(), sorted(pending_keys)], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def cloud_limit(self, settings: dict[str, Any]) -> Optional[int]:
        value = settings.get("max_cloud_chars")
        if value is None:
            app = self.db.get_setting("app") or {}
            value = app.get("max_cloud_chars_per_document", DEFAULT_CLOUD_LIMIT)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return DEFAULT_CLOUD_LIMIT
        return value if value > 0 else None

    def _voice(self, provider: TTSProvider, sel: VoiceSelection) -> Optional[Voice]:
        try:
            return next((v for v in provider.list_voices() if v.id == sel.voice_id), None)
        except TTSError:
            return None

    # -- estimate -------------------------------------------------------------
    def estimate(self, project_id: str) -> dict[str, Any]:
        _, settings, plan, sel, provider = self.plan(project_id)
        caps = provider.get_provider_capabilities()
        keys = self.keys(plan, sel)
        pending = [i for i, k in enumerate(keys) if not self.is_cached(project_id, k)]
        pending_chars = sum(len(plan.segments[i].text) for i in pending)
        usage = provider.estimate_usage(pending_chars, sel.model_id)
        voice = self._voice(provider, sel)
        limit = self.cloud_limit(settings) if caps.is_paid else None
        used = self.db.usage_for_project(project_id, provider.id) if caps.is_paid else 0
        model_name = None
        if sel.model_id:
            model_name = next((m.name for m in provider.list_models() if m.id == sel.model_id), sel.model_id)
        return {
            "provider": {"id": caps.id, "name": caps.name, "is_paid": caps.is_paid, "is_local": caps.is_local},
            "voice": {"id": sel.voice_id, "name": voice.name if voice else sel.voice_id},
            "model": {"id": sel.model_id, "name": model_name} if sel.model_id else None,
            "language": sel.language,
            "segments_total": len(plan.segments),
            "segments_cached": len(plan.segments) - len(pending),
            "segments_pending": len(pending),
            "characters_total": plan.total_chars,
            "characters_pending": pending_chars,
            "usage": usage.to_dict(),
            "limit": limit,
            "used_on_document": used,
            "exceeds_limit": bool(limit is not None and used + pending_chars > limit),
            "confirmation_token": self._token(project_id, sel, [keys[i] for i in pending]),
            "timestamps_note": caps.timestamps_note,
            "chapters": len(plan.chapters),
        }

    # -- generation -------------------------------------------------------------
    def _synthesize_with_retry(self, provider: TTSProvider, request: SynthesisRequest, job: Job,
                               attempts: int = 4) -> SynthesisResult:
        delay = 2.0
        for attempt in range(attempts):
            try:
                return provider.synthesize(request, job.cancel_event)
            except TTSError as exc:
                if exc.kind == "cancelled":
                    raise CancelledError() from exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                job.update(message=f"{exc.message} Prøver igjen om {int(delay)} sekunder …")
                if job.cancel_event.wait(delay):
                    raise CancelledError() from exc
                delay *= 2
        raise AssertionError("unreachable")

    def generate(self, job: Job, project_id: str, confirmation_token: Optional[str] = None) -> dict[str, Any]:
        document, settings, plan, sel, provider = self.plan(project_id)
        caps = provider.get_provider_capabilities()
        if not plan.segments:
            raise UserFacingError(
                "Det er ingen tekst å lese opp med de valgene du har gjort. Sjekk tekstvalgene.",
                code="nothing_to_read",
            )
        keys = self.keys(plan, sel)
        pending = [i for i, k in enumerate(keys) if not self.is_cached(project_id, k)]
        pending_chars = sum(len(plan.segments[i].text) for i in pending)

        if caps.is_paid and pending:
            expected = self._token(project_id, sel, [keys[i] for i in pending])
            if not confirmation_token or confirmation_token != expected:
                raise UserFacingError(
                    "Du må godkjenne overslaget før teksten sendes til en betalt tjeneste. "
                    "Teksten eller innstillingene kan ha endret seg – se over overslaget på nytt.",
                    code="confirmation_required",
                    status=409,
                )
            limit = self.cloud_limit(settings)
            used = self.db.usage_for_project(project_id, provider.id)
            if limit is not None and used + pending_chars > limit:
                raise UserFacingError(
                    f"Dette ville overskride forbruksgrensen for dokumentet ({limit:,} tegn). "
                    "Øk grensen eller velg færre deler av teksten.".replace(",", " "),
                    code="spending_limit",
                )

        total = len(pending)
        started = time.time()
        sent_chars = 0
        _LOG.info("Generating %d/%d segments for project %s with %s", total, len(keys), project_id, sel.provider_id)
        for n, i in enumerate(pending):
            job.check_cancel()
            seg = plan.segments[i]
            eta = ""
            if n >= 2:
                per_seg = (time.time() - started) / n
                remaining = per_seg * (total - n)
                eta = f" – ca. {max(1, round(remaining / 60))} min igjen" if remaining > 60 else " – under ett minutt igjen"
            job.update(progress=0.9 * n / max(1, total), message=f"Lager lyd: del {n + 1} av {total}{eta}")
            if caps.is_paid:
                if sent_chars + len(seg.text) > pending_chars:
                    raise TTSError("Forbruksgrensen for jobben er nådd.", kind="spending_limit")
            request = SynthesisRequest(
                text=seg.text,
                voice_id=sel.voice_id,
                language=sel.language,
                model_id=sel.model_id,
                speed=sel.speed,
                previous_text=plan.segments[i - 1].text if i > 0 else None,
                next_text=plan.segments[i + 1].text if i + 1 < len(plan.segments) else None,
            )
            result = self._synthesize_with_retry(provider, request, job)
            wav_path, meta_path = self._segment_paths(project_id, keys[i])
            write_wav(wav_path, result.pcm, result.sample_rate)
            write_json_atomic(meta_path, {
                "precision": result.precision,
                "words": [[w.start_char, w.end_char, round(w.start, 4), round(w.end, 4)] for w in result.words],
                "sample_rate": result.sample_rate,
                "provider": sel.provider_id,
                "paid": caps.is_paid,
                "chars": len(seg.text),
                "chars_billed": result.chars_billed,
                "request_id": result.request_id,
                "created_at": time.time(),
            })
            if caps.is_paid:
                # The limit counts characters Medleser sends (what the user approved); the
                # service's own cost figure is kept in the segment metadata for reference.
                sent_chars += len(seg.text)
                self.db.add_usage(project_id, provider.id, len(seg.text), sel.voice_id, sel.model_id)

        job.check_cancel()
        job.update(progress=0.92, message="Setter sammen lydfilen …")
        manifest = self.assemble(project_id, document, settings, plan, sel, provider, keys)
        job.update(progress=1.0, message="Lyden er klar.")
        return {"duration": manifest["duration"], "precision": manifest["precision"],
                "generated_segments": total, "segments": len(keys)}

    # -- assembly -----------------------------------------------------------------
    def assemble(self, project_id: str, document: Document, settings: dict[str, Any], plan: ReadingPlan,
                 sel: VoiceSelection, provider: TTSProvider, keys: list[str]) -> dict[str, Any]:
        caps = provider.get_provider_capabilities()
        voice = self._voice(provider, sel)
        voice_name = voice.name if voice else sel.voice_id
        out_path = self.store.audio_path(project_id)
        writer: Optional[Mp3Writer] = None
        sample_rate = 0
        samples = 0
        words: list[list[Any]] = []
        segments: list[list[Any]] = []
        precisions: list[str] = []
        try:
            for i, seg in enumerate(plan.segments):
                wav_path, meta_path = self._segment_paths(project_id, keys[i])
                meta = read_json(meta_path) or {}
                pcm, rate = read_wav(wav_path)
                if writer is None:
                    sample_rate = rate
                    writer = Mp3Writer(out_path, sample_rate, tags=Mp3Tags(
                        title=document.title or "Medleser",
                        album=document.title or "Medleser",
                        artist=f"Medleser – {voice_name}",
                        comment=f"Laget med Medleser. Stemme: {voice_name} ({caps.name}).",
                    ))
                if rate != sample_rate:
                    pcm = resample(pcm, rate, sample_rate)
                pcm = apply_fades(pcm, sample_rate)
                t0 = samples / sample_rate
                writer.write(pcm)
                samples += len(pcm)
                t1 = samples / sample_rate
                precision = meta.get("precision", "segment")
                precisions.append(precision)
                # Word times are seconds relative to the segment start (unchanged by resampling).
                for cs, ce, ws, we in meta.get("words", []):
                    mapped = seg.map_range(int(cs), int(ce))
                    if mapped is None:
                        continue
                    block_id, part, ds, de = mapped
                    words.append([round(t0 + ws, 3), round(t0 + we, 3), i, block_id, part, ds, de])
                segments.append([round(t0, 3), round(t1, 3), seg.block_id, precision])
                pause = seg.pause_after if i + 1 < len(plan.segments) else 0.4
                gap = silence(pause, sample_rate)
                writer.write(gap)
                samples += len(gap)
            assert writer is not None
            writer.close()
        except Exception:
            if writer is not None:
                writer.abort()
            raise

        duration = samples / float(sample_rate)
        info = probe_mp3(out_path)
        if not info.ok or abs(info.duration - duration) > max(1.0, duration * 0.02):
            raise UserFacingError(
                "Lydfilen ble ikke riktig laget. Prøv å lage lyden på nytt.",
                code="mp3_invalid",
                details=f"probe={info} expected={duration:.2f}s",
            )

        if all(p == "word" for p in precisions):
            precision = "word"
        elif any(p == "word" for p in precisions):
            precision = "mixed"
        else:
            precision = "segment"
        note = caps.timestamps_note
        if precision == "mixed":
            note += " Noen avsnitt markeres som helhet fordi ordtidene der ikke kunne kobles sikkert."
        elif precision == "segment":
            note = "Avsnitt for avsnitt: denne stemmen gir ikke tidspunkter per ord, så hele avsnittet markeres."

        blocks: dict[str, list[float]] = {}
        for t0, t1, block_id, _ in segments:
            if block_id in blocks:
                blocks[block_id][1] = t1
            else:
                blocks[block_id] = [t0, t1]
        chapters = []
        for ch in plan.chapters:
            if ch["segment"] < len(segments):
                chapters.append({"title": ch["title"], "block_id": ch["block_id"], "t0": segments[ch["segment"]][0],
                                 "segment": ch["segment"]})

        manifest = {
            "format": "medleser-sync",
            "version": 1,
            "provider": sel.provider_id,
            "provider_name": caps.name,
            "voice_id": sel.voice_id,
            "voice_name": voice_name,
            "model_id": sel.model_id,
            "language": sel.language,
            "speed": sel.speed,
            "sample_rate": sample_rate,
            "duration": round(duration, 3),
            "precision": precision,
            "precision_note": note,
            "audio_offset": round(lame_delay_seconds(sample_rate), 5),
            "segments": segments,
            "words": words,
            "blocks": blocks,
            "chapters": chapters,
            "plan_hash": self.plan_hash(keys),
            "generated_at": time.time(),
        }
        write_json_atomic(self.store.sync_path(project_id), manifest)
        self.store.update_settings(project_id, audio={
            **sel.to_dict(),
            "voice_name": voice_name,
            "provider_name": caps.name,
            "duration": round(duration, 3),
            "precision": precision,
            "plan_hash": manifest["plan_hash"],
            "generated_at": manifest["generated_at"],
        })
        self.db.update_project(project_id, audio_state="ready", audio_duration=round(duration, 3))
        if not caps.is_paid:
            self._prune_unused(project_id, set(keys))
        return manifest

    def _prune_unused(self, project_id: str, keep: set[str]) -> None:
        """Remove cached free segments that the current plan no longer uses."""
        for meta_path in self.store.segments_dir(project_id).glob("*.json"):
            key = meta_path.stem
            if key in keep:
                continue
            meta = read_json(meta_path) or {}
            if meta.get("paid"):
                continue  # paid audio is kept until the project is deleted
            meta_path.unlink(missing_ok=True)
            meta_path.with_suffix(".wav").unlink(missing_ok=True)

    # -- state ----------------------------------------------------------------------
    def audio_state(self, project_id: str) -> dict[str, Any]:
        """Is the audio ready, partially generated, stale (text changed) or missing?"""
        settings = self.store.load_settings(project_id)
        audio = settings.get("audio")
        ready = bool(audio) and self.store.audio_path(project_id).exists() and self.store.sync_path(project_id).exists()
        state: dict[str, Any] = {"ready": ready, "audio": audio, "stale": False, "cached_segments": 0,
                                 "total_segments": 0}
        try:
            _, _, plan, sel, _ = self.plan(project_id)
        except UserFacingError:
            return state
        keys = self.keys(plan, sel)
        state["total_segments"] = len(keys)
        state["cached_segments"] = sum(1 for k in keys if self.is_cached(project_id, k))
        if ready:
            state["stale"] = audio.get("plan_hash") != self.plan_hash(keys)
        return state

    # -- export ------------------------------------------------------------------------
    def export_chapters(self, project_id: str, dest_dir: Path, job: Optional[Job] = None) -> list[Path]:
        """Write one MP3 per chapter (re-encoded from the cached segments)."""
        from ..storage.projects import safe_filename

        document, settings, plan, sel, provider = self.plan(project_id)
        keys = self.keys(plan, sel)
        if not all(self.is_cached(project_id, k) for k in keys):
            raise UserFacingError("Lag lyden først, så kan du lagre den per kapittel.", code="audio_missing")
        if not plan.chapters:
            raise UserFacingError(
                "Medleser fant ingen kapitler i dette dokumentet. Lagre hele dokumentet som én MP3-fil i stedet.",
                code="no_chapters",
            )
        bounds: list[tuple[str, int, int]] = []
        first = plan.chapters[0]["segment"]
        if first > 0:
            # Name the part before the first chapter after its own heading (usually the
            # document title) rather than a fixed word that may clash with a chapter name.
            intro = document.title or "Start"
            for seg in plan.segments[:first]:
                block = document.block_by_id(seg.block_id)
                if block is not None and block.kind == "heading":
                    intro = " ".join(block.text.split()) or intro
                    break
            bounds.append((intro, 0, first))
        for n, ch in enumerate(plan.chapters):
            end = plan.chapters[n + 1]["segment"] if n + 1 < len(plan.chapters) else len(plan.segments)
            if end > ch["segment"]:
                bounds.append((ch["title"], ch["segment"], end))
        dest_dir.mkdir(parents=True, exist_ok=True)
        voice = self._voice(provider, sel)
        voice_name = voice.name if voice else sel.voice_id
        written: list[Path] = []
        for n, (title, start, end) in enumerate(bounds, start=1):
            if job is not None:
                job.check_cancel()
                job.update(progress=(n - 1) / len(bounds), message=f"Lagrer kapittel {n} av {len(bounds)} …")
            name = safe_filename(f"{n:02d} {title[:80]}.mp3")
            target = dest_dir / name
            writer: Optional[Mp3Writer] = None
            try:
                for i in range(start, end):
                    pcm, rate = read_wav(self._segment_paths(project_id, keys[i])[0])
                    if writer is None:
                        writer = Mp3Writer(target, rate, tags=Mp3Tags(
                            title=title, album=document.title, artist=f"Medleser – {voice_name}",
                            track=f"{n}/{len(bounds)}",
                        ))
                        sample_rate = rate
                    if rate != sample_rate:
                        pcm = resample(pcm, rate, sample_rate)
                    writer.write(apply_fades(pcm, sample_rate))
                    writer.write(silence(plan.segments[i].pause_after if i + 1 < end else 0.4, sample_rate))
                if writer is not None:
                    writer.close()
                    written.append(target)
            except Exception:
                if writer is not None:
                    writer.abort()
                raise
        return written

    # -- samples ----------------------------------------------------------------------
    def sample(self, cache_dir: Path, provider_id: str, voice_id: str, language: str,
               text: Optional[str] = None, model_id: Optional[str] = None,
               cancel=None) -> tuple[Path, bool]:
        """Return (path, newly_generated). A cached sample costs nothing."""
        provider = self.providers.get(provider_id)
        lang = (language or "nb").split("-")[0]
        sample_text = (text or SAMPLE_TEXTS.get(lang) or SAMPLE_TEXTS["nb"]).strip()[:400]
        key = segment_cache_key(sample_text, provider_id, voice_id, model_id, lang, 1.0, "sample-1")
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"sample-{key}.wav"
        if path.exists():
            return path, False
        result = provider.synthesize(
            SynthesisRequest(text=sample_text, voice_id=voice_id, language=lang, model_id=model_id), cancel
        )
        write_wav(path, apply_fades(result.pcm, result.sample_rate), result.sample_rate)
        return path, True
