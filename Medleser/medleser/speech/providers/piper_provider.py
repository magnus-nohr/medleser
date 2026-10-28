"""Free local neural voices with Piper (runs entirely on this computer)."""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np

from . import piper_catalog as catalog
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
from .piper_align import TimedPhoneme, align_words, phoneme_words

_LOG = logging.getLogger(__name__)

# Silence inserted between sentences that Piper synthesizes separately.
SENTENCE_PAUSE_S = 0.12
# Leave head-room below full scale (Piper peak-normalizes every sentence).
OUTPUT_VOLUME = 0.8


def split_voice_id(voice_id: str) -> tuple[str, Optional[str]]:
    key, _, speaker = voice_id.partition("#")
    return key, (speaker or None)


class PiperProvider(TTSProvider):
    id = "piper"

    def __init__(self, voices_root: Path, max_loaded: int = 2) -> None:
        self.root = voices_root
        self._loaded: "OrderedDict[str, object]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_loaded = max_loaded

    # -- metadata ----------------------------------------------------------
    def get_provider_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            id=self.id,
            name="Lokale KI-stemmer (Piper)",
            description=(
                "Gratis nevrale stemmer som lages på denne datamaskinen. Teksten sendes ikke "
                "noe sted. Stemmene må lastes ned én gang (60–115 MB per stemme)."
            ),
            is_local=True,
            is_paid=False,
            auth_method="none",
            timestamps="word",
            timestamps_note=(
                "Ord for ord: tidspunktene kommer fra stemmemodellens egne beregninger av hvor lenge "
                "hver lyd varer. Hvis et ord ikke kan kobles sikkert, markeres avsnittet i stedet."
            ),
            max_chars_per_request=3000,
            preferred_segment_chars=700,
            supports_cancel=True,
            supports_speed=True,
            languages=tuple(sorted({v.language for v in catalog.CATALOG})),
            pricing_note="Gratis",
            website="https://github.com/OHF-Voice/piper1-gpl",
        )

    def status(self) -> ProviderStatus:
        keys = catalog.installed_keys(self.root)
        if not keys:
            return ProviderStatus(
                "needs_setup",
                "Ingen lokale stemmer er lastet ned ennå. Last ned en norsk stemme for å bruke gratis "
                "opplesning uten internett.",
            )
        return ProviderStatus("ready", f"{len(keys)} stemme(r) er lastet ned og klare.")

    def check_connection(self) -> ProviderStatus:
        return self.status()

    def list_voices(self, language: Optional[str] = None) -> list[Voice]:
        voices: list[Voice] = []
        for entry in catalog.CATALOG:
            if not catalog.is_installed(self.root, entry.key):
                continue
            manifest = catalog.read_manifest(self.root, entry.key) or {}
            aligned = bool(manifest.get("aligned"))
            precision_note = "Ord-for-ord-markering." if aligned else "Avsnittsmarkering (ingen ordtider)."
            base_note = (
                "Lokal stemme. Kvaliteten er ikke testet systematisk av Medleser – hør en stemmeprøve. "
                + precision_note
            )
            if entry.speaker_labels:
                for code, label in entry.speaker_labels.items():
                    voice = Voice(
                        id=f"{entry.key}#{code}",
                        name=f"{entry.name.split(' (')[0]} – {label}",
                        language=entry.language,
                        languages=(entry.language,),
                        gender=None,
                        description=entry.description,
                        quality_note=base_note,
                        extra={"key": entry.key, "speaker": code},
                    )
                    if language is None or voice.speaks(language):
                        voices.append(voice)
            else:
                voice = Voice(
                    id=entry.key,
                    name=entry.name,
                    language=entry.language,
                    languages=(entry.language,),
                    gender=entry.gender,
                    description=entry.description,
                    quality_note=base_note,
                    extra={"key": entry.key},
                )
                if language is None or voice.speaks(language):
                    voices.append(voice)
        return voices

    # -- synthesis ---------------------------------------------------------
    def _load(self, key: str):
        if key not in catalog.CATALOG_BY_KEY:
            raise TTSError("Ukjent stemme.", kind="voice_not_found")
        with self._lock:
            if key in self._loaded:
                self._loaded.move_to_end(key)
                return self._loaded[key]
        if not catalog.is_installed(self.root, key):
            raise TTSError(
                "Denne stemmen er ikke lastet ned. Last den ned under «Stemmer og KI-tjenester».",
                kind="voice_not_found",
            )
        try:
            from piper import PiperVoice

            voice = PiperVoice.load(catalog.model_path(self.root, key))
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "Stemmen kunne ikke startes. Prøv å laste den ned på nytt.",
                kind="unavailable",
                details=repr(exc),
            ) from exc
        with self._lock:
            self._loaded[key] = voice
            while len(self._loaded) > self._max_loaded:
                self._loaded.popitem(last=False)
        return voice

    def synthesize(
        self, request: SynthesisRequest, cancel: Optional[threading.Event] = None
    ) -> SynthesisResult:
        from piper import SynthesisConfig

        key, speaker = split_voice_id(request.voice_id)
        voice = self._load(key)
        speaker_id = None
        if speaker is not None:
            speaker_id = voice.config.speaker_id_map.get(speaker)
            if speaker_id is None:
                raise TTSError("Ukjent stemmevariant.", kind="voice_not_found", details=speaker)
        speed = max(0.5, min(2.0, float(request.speed or 1.0)))
        syn_config = SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=1.0 / speed,
            normalize_audio=True,
            volume=OUTPUT_VOLUME,
        )
        sample_rate = int(voice.config.sample_rate)
        pause = np.zeros(int(SENTENCE_PAUSE_S * sample_rate), dtype=np.int16)

        pieces: list[np.ndarray] = []
        stream: list[TimedPhoneme] = []
        offset = 0
        alignment_ok = True
        try:
            for i, chunk in enumerate(voice.synthesize(request.text, syn_config, include_alignments=True)):
                if cancel is not None and cancel.is_set():
                    raise TTSError("Avbrutt.", kind="cancelled")
                if i > 0:
                    pieces.append(pause)
                    offset += len(pause)
                audio = chunk.audio_int16_array
                if chunk.phoneme_alignments is None:
                    alignment_ok = False
                else:
                    pos = offset
                    for al in chunk.phoneme_alignments:
                        stream.append(TimedPhoneme(al.phoneme, pos, pos + int(al.num_samples)))
                        pos += int(al.num_samples)
                pieces.append(audio)
                offset += len(audio)
        except TTSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TTSError(
                "Den lokale stemmen klarte ikke å lese denne teksten.",
                kind="server",
                details=repr(exc),
            ) from exc

        pcm = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.int16)
        words: list[WordTiming] = []
        precision = "segment"
        if alignment_ok and stream:

            def phonemize_word(word: str) -> str:
                return "".join("".join(sentence) for sentence in voice.phonemize(word))

            aligned = align_words(request.text, phoneme_words(stream), phonemize_word)
            if aligned:
                sr = float(sample_rate)
                words = [WordTiming(cs, ce, s / sr, e / sr) for cs, ce, s, e in aligned]
                precision = "word"
        return SynthesisResult(pcm=pcm, sample_rate=sample_rate, precision=precision, words=words)

    def close(self) -> None:
        with self._lock:
            self._loaded.clear()


def voice_config(root: Path, key: str) -> dict:
    path = catalog.voice_dir(root, key) / f"{key}.onnx.json"
    return json.loads(path.read_text(encoding="utf-8"))
