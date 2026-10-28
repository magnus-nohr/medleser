"""Built-in Windows voices (OneCore) via WinRT ``SpeechSynthesizer``.

Free, offline and already installed on Windows. The engine reports a word
boundary cue for every word (start time + character positions in the input),
which gives authentic word-level timing for the read-along view.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import threading
import wave
from typing import Optional

import numpy as np

from .base import (
    ProviderCapabilities,
    ProviderStatus,
    SynthesisRequest,
    SynthesisResult,
    TTSError,
    TTSProvider,
    Voice,
    WordTiming,
)

_LOG = logging.getLogger(__name__)
_thread_state = threading.local()


def _ensure_apartment() -> None:
    """Pin the process-wide MTA and join it once per worker thread."""
    from ...winrt_support import ensure_mta

    ensure_mta()
    if getattr(_thread_state, "initialized", False):
        return
    try:
        from winrt.runtime import ApartmentType, init_apartment

        init_apartment(ApartmentType.MULTI_THREADED)
    except Exception:  # noqa: BLE001 - already initialized (e.g. STA GUI thread) is fine
        pass
    _thread_state.initialized = True


def _run(coro):
    """Run a WinRT coroutine to completion from synchronous worker code."""
    _ensure_apartment()
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _language_code(tag: str) -> str:
    base = (tag or "").split("-")[0].lower()
    return "nb" if base in ("nb", "no") else base


def _utf16_to_py_index(text: str) -> list[int]:
    """Map UTF-16 code-unit index -> Python string index (handles emoji etc.)."""
    mapping: list[int] = []
    for i, ch in enumerate(text):
        mapping.append(i)
        if ord(ch) > 0xFFFF:
            mapping.append(i)
    mapping.append(len(text))
    return mapping


class WindowsVoiceProvider(TTSProvider):
    id = "windows"

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._error: Optional[str] = None

    # -- availability ------------------------------------------------------
    def _voices_raw(self):
        if sys.platform != "win32":
            raise TTSError("Windows-stemmer finnes bare på Windows.", kind="unavailable")
        _ensure_apartment()
        try:
            from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "Windows-stemmene kunne ikke startes på denne maskinen.",
                kind="unavailable",
                details=repr(exc),
            ) from exc
        _ensure_apartment()
        return list(SpeechSynthesizer.all_voices)

    def get_provider_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            id=self.id,
            name="Windows-stemmer",
            description=(
                "Stemmene som følger med Windows. Gratis, virker uten internett og "
                "teksten forlater ikke datamaskinen."
            ),
            is_local=True,
            is_paid=False,
            auth_method="none",
            timestamps="word",
            timestamps_note=(
                "Ord for ord: Windows oppgir når hvert ord starter. Markeringen følger disse "
                "tidspunktene."
            ),
            max_chars_per_request=5000,
            preferred_segment_chars=1200,
            supports_cancel=False,
            supports_speed=True,
            languages=("nb", "en"),
            pricing_note="Gratis",
        )

    def status(self) -> ProviderStatus:
        try:
            voices = self._voices_raw()
        except TTSError as exc:
            return ProviderStatus("unavailable", exc.message, exc.details)
        if not voices:
            return ProviderStatus(
                "needs_setup",
                "Ingen Windows-stemmer er installert. Du kan legge til stemmer i Windows-innstillingene "
                "under Tid og språk > Tale.",
            )
        has_norwegian = any(_language_code(v.language) == "nb" for v in voices)
        msg = f"{len(voices)} stemmer er klare."
        if not has_norwegian:
            msg += " Ingen norsk stemme funnet – legg til norsk tale i Windows-innstillingene."
        return ProviderStatus("ready", msg)

    def check_connection(self) -> ProviderStatus:
        return self.status()

    def list_voices(self, language: Optional[str] = None) -> list[Voice]:
        try:
            raw = self._voices_raw()
        except TTSError:
            return []
        voices: list[Voice] = []
        for v in raw:
            lang = _language_code(v.language)
            gender = "female" if int(v.gender) == 1 else "male"
            voice = Voice(
                id=v.id,
                name=v.display_name.replace("Microsoft ", ""),
                language=lang,
                languages=(lang,),
                gender=gender,
                description=f"Windows-stemme ({v.language})",
                quality_note="Syntetisk Windows-stemme. Tydelig, men mindre naturlig enn KI-stemmer.",
                extra={"tag": v.language},
            )
            if language is None or voice.speaks(language):
                voices.append(voice)
        voices.sort(key=lambda x: (x.language != "nb", x.name))
        return voices

    # -- synthesis ---------------------------------------------------------
    def synthesize(
        self, request: SynthesisRequest, cancel: Optional[threading.Event] = None
    ) -> SynthesisResult:
        if not request.text.strip():
            return SynthesisResult(np.zeros(0, dtype=np.int16), 16000, "word", [])
        if cancel is not None and cancel.is_set():
            raise TTSError("Avbrutt.", kind="cancelled")
        try:
            return _run(self._synthesize_async(request))
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "Windows-stemmen klarte ikke å lese denne teksten.",
                kind="server",
                details=repr(exc),
            ) from exc

    async def _synthesize_async(self, request: SynthesisRequest) -> SynthesisResult:
        from winrt.windows.media.core import SpeechCue
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader

        synth = SpeechSynthesizer()
        voice = next((v for v in SpeechSynthesizer.all_voices if v.id == request.voice_id), None)
        if voice is None:
            raise TTSError(
                "Den valgte Windows-stemmen finnes ikke lenger på denne maskinen. Velg en annen stemme.",
                kind="voice_not_found",
            )
        synth.voice = voice
        synth.options.include_word_boundary_metadata = True
        synth.options.include_sentence_boundary_metadata = False
        synth.options.speaking_rate = max(0.5, min(3.0, float(request.speed or 1.0)))

        stream = await synth.synthesize_text_to_stream_async(request.text)
        size = int(stream.size)
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)

        with wave.open(io.BytesIO(bytes(buf))) as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
        if width != 2:
            raise TTSError("Uventet lydformat fra Windows-stemmen.", kind="server", details=f"width={width}")
        pcm = np.frombuffer(frames, dtype="<i2").astype(np.int16)
        if channels > 1:
            pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
        duration = len(pcm) / float(sample_rate)

        index_map = _utf16_to_py_index(request.text)
        starts: list[tuple[float, int, int]] = []
        for track in stream.timed_metadata_tracks:
            if track.id != "SpeechWord":
                continue
            for raw in track.cues:
                cue = raw.as_(SpeechCue)
                start_pos = cue.start_position_in_input
                end_pos = cue.end_position_in_input
                if start_pos is None or end_pos is None:
                    continue
                s16, e16 = int(start_pos), int(end_pos) + 1  # end is inclusive
                s = index_map[min(s16, len(index_map) - 1)]
                e = index_map[min(e16, len(index_map) - 1)]
                if e <= s:
                    continue
                starts.append((cue.start_time.total_seconds(), s, e))
        starts.sort()
        words: list[WordTiming] = []
        for i, (t, s, e) in enumerate(starts):
            t_next = starts[i + 1][0] if i + 1 < len(starts) else duration
            words.append(WordTiming(s, e, max(0.0, t), max(t, min(t_next, duration))))
        precision = "word" if words else "segment"
        return SynthesisResult(pcm=pcm, sample_rate=sample_rate, precision=precision, words=words)
