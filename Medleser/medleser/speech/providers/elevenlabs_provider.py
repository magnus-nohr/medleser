"""ElevenLabs text-to-speech (paid cloud service, user's own API key).

* Authentication: the user's API key, read from Windows Credential Manager
  on every call (never cached on disk, never logged, never sent to the UI).
* Connection test and voice listing use free endpoints only.
* Synthesis uses ``/v1/text-to-speech/{voice_id}/with-timestamps``, which
  returns character-level timestamps; we map them to words.
* The generator only calls :meth:`synthesize` after the user has approved an
  estimate (see ``generator.AudioService``).
"""

from __future__ import annotations

import base64
import difflib
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
import numpy as np

from .base import (
    ModelInfo,
    ProviderCapabilities,
    ProviderStatus,
    SynthesisRequest,
    SynthesisResult,
    TTSError,
    TTSProvider,
    UsageEstimate,
    Voice,
    WordTiming,
)
from .piper_align import WORD_RE

_LOG = logging.getLogger(__name__)

API_BASE = "https://api.elevenlabs.io"
PCM_FORMAT = "pcm_24000"
PCM_RATE = 24000
FALLBACK_FORMAT = "mp3_44100_128"
PREVIEW_HOSTS = ("storage.googleapis.com", "elevenlabs.io")

# Fallback model list (from ElevenLabs' model documentation, October 2026). When the
# key may read /v1/models, the live list (languages, limits, cost multipliers) is used.
_TIMESTAMP_NOTE_UNCONFIRMED = (
    " Medleser har ikke bekreftet at modellen sender tidspunkter per tegn. Gjør den ikke det, markeres "
    "avsnitt i stedet for ord."
)
MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="eleven_flash_v2_5",
        name="Flash v2.5 (rask og rimelig)",
        description="Støtter norsk. Omtrent halv pris per tegn sammenlignet med de største modellene. "
        "Tall og forkortelser leses ikke alltid like naturlig.",
        languages=("no", "en", "sv", "da", "de", "fr", "es", "nl", "fi", "pl", "it", "pt"),
        max_chars_per_request=40000,
        credits_per_char=0.5,
    ),
    ModelInfo(
        id="eleven_v4",
        name="Eleven v4 (nyeste, høyest kvalitet)",
        description="ElevenLabs sin nyeste og mest naturlige modell. Støtter norsk bokmål." + _TIMESTAMP_NOTE_UNCONFIRMED,
        languages=("nob", "no", "en", "sv", "da", "de", "fr", "es"),
        max_chars_per_request=10000,
        credits_per_char=None,
        supports_timestamps=False,
    ),
    ModelInfo(
        id="eleven_v3",
        name="Eleven v3 (uttrykksfull)",
        description="Levende opplesing med mye innlevelse. Støtter norsk." + _TIMESTAMP_NOTE_UNCONFIRMED,
        languages=("nor", "no", "en", "sv", "da", "de", "fr", "es"),
        max_chars_per_request=5000,
        credits_per_char=None,
        supports_timestamps=False,
    ),
    ModelInfo(
        id="eleven_multilingual_v2",
        name="Multilingual v2 (stabil for lange tekster)",
        description="Stabil for lange tekster på 29 språk, men ikke norsk. Omtrent 1 kreditt per tegn.",
        languages=("en", "sv", "da", "de", "fr", "es", "nl", "fi", "pl", "it", "pt"),
        max_chars_per_request=10000,
        credits_per_char=1.0,
    ),
)
MODELS_BY_ID = {m.id: m for m in MODELS}
_MODEL_ORDER = [m.id for m in MODELS]
NO_LANGUAGE_CODE_MODELS = {"eleven_multilingual_v2"}  # rejects/ignores language_code
SEGMENT_HARD_MAX = 4800  # keep requests (and the cost of a retry) moderate
_LANGUAGE_ALIASES = {
    "nb": {"no", "nb", "nob", "nor"},
    "nn": {"no", "nn", "nno", "nor"},
    "en": {"en", "eng"},
    "sv": {"sv", "swe"},
    "da": {"da", "dan"},
    "de": {"de", "deu", "ger"},
    "fr": {"fr", "fra", "fre"},
    "es": {"es", "spa"},
}


def _language_code(language: str) -> Optional[str]:
    base = (language or "").split("-")[0].lower()
    if base in ("nb", "nn", "no"):
        return "no"
    return base if len(base) == 2 else None


def _model_supports(model: ModelInfo, language: Optional[str]) -> bool:
    if not language:
        return True
    base = language.split("-")[0].lower()
    wanted = _LANGUAGE_ALIASES.get(base, {base})
    langs = {lang.lower() for lang in model.languages}
    return "multi" in langs or bool(langs & wanted)


class ElevenLabsProvider(TTSProvider):
    id = "elevenlabs"

    def __init__(self, secrets, *, transport: Optional[httpx.BaseTransport] = None,
                 base_url: str = API_BASE) -> None:
        self.secrets = secrets
        self._transport = transport
        self._base_url = base_url
        self._lock = threading.Lock()
        self._voices: Optional[tuple[float, list[Voice]]] = None
        self._models: Optional[list[ModelInfo]] = None  # live list from /v1/models
        self._account: dict[str, Any] = {}
        self._last_status: Optional[ProviderStatus] = None

    # -- helpers ---------------------------------------------------------------
    def _key(self) -> str:
        key = self.secrets.get(self.id)
        if not key:
            raise TTSError("ElevenLabs er ikke tilkoblet. Koble til under «Stemmer og KI-tjenester».",
                           kind="auth")
        return key

    def _client(self, key: Optional[str] = None, timeout: float = 30.0) -> httpx.Client:
        headers = {"User-Agent": "Medleser/1.0"}
        if key:
            headers["xi-api-key"] = key
        return httpx.Client(base_url=self._base_url, headers=headers, transport=self._transport,
                            timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False)

    @staticmethod
    def _detail(response: httpx.Response) -> tuple[str, str]:
        try:
            data = response.json()
        except ValueError:
            return "", response.text[:200]
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, dict):
            return str(detail.get("status") or detail.get("code") or ""), str(detail.get("message") or "")[:300]
        if isinstance(detail, list) and detail:
            return "validation", str(detail[0])[:300]
        return "", str(detail or data)[:300]

    def _error(self, response: httpx.Response) -> TTSError:
        status, message = self._detail(response)
        details = f"HTTP {response.status_code} {status}: {message}".strip()
        code = response.status_code
        if status == "quota_exceeded" or code == 402:
            return TTSError(
                "Du har ikke nok kreditter igjen hos ElevenLabs til denne teksten. Det som allerede er laget, "
                "er lagret. Du kan vente til kvoten fornyes, oppgradere kontoen hos ElevenLabs, eller velge en "
                "gratis lokal stemme.",
                kind="quota", details=details)
        if status in ("missing_permissions", "insufficient_permissions") or code == 403:
            return TTSError(
                "Tilgangsnøkkelen mangler tillatelse til dette. Gi nøkkelen tilgang til «Text to Speech» og "
                "«Voices» (lese) hos ElevenLabs, eller lag en ny nøkkel med disse tillatelsene.",
                kind="permission", details=details)
        if status == "detected_unusual_activity":
            return TTSError(
                "ElevenLabs har midlertidig stengt gratisbruk for kontoen din (uvanlig aktivitet). "
                "Les mer på kontoen din hos ElevenLabs.",
                kind="auth", details=details)
        if code == 401 or status in ("invalid_api_key", "needs_authorization"):
            return TTSError(
                "ElevenLabs godtok ikke tilgangsnøkkelen. Sjekk at du har kopiert hele nøkkelen, eller lag en ny.",
                kind="auth", details=details)
        if code == 404 or status == "voice_not_found":
            return TTSError("Stemmen finnes ikke lenger hos ElevenLabs. Velg en annen stemme.",
                            kind="voice_not_found", details=details)
        if code == 429 or status in ("too_many_concurrent_requests", "system_busy", "rate_limit_exceeded"):
            return TTSError("ElevenLabs har mye å gjøre akkurat nå.", kind="rate_limit", details=details,
                            retryable=True)
        if code >= 500:
            return TTSError("ElevenLabs svarer ikke som det skal akkurat nå.", kind="server", details=details,
                            retryable=True)
        return TTSError("ElevenLabs avviste forespørselen.", kind="invalid_request", details=details)

    @staticmethod
    def _network_error(exc: Exception) -> TTSError:
        return TTSError("Får ikke kontakt med ElevenLabs. Sjekk internettforbindelsen og prøv igjen.",
                        kind="network", details=type(exc).__name__, retryable=True)

    @staticmethod
    def _synthesis_network_error(exc: Exception) -> TTSError:
        """Only retry automatically when the request can't have reached ElevenLabs.

        After a read timeout or a broken response the request may already have been
        processed – and charged – so it is not repeated without the user.
        """
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return ElevenLabsProvider._network_error(exc)
        return TTSError(
            "Svaret fra ElevenLabs kom ikke frem. Denne delen ble ikke lagret, og ElevenLabs kan ha trukket "
            "kreditter for den. Trykk «Fortsett å lage lyd» når forbindelsen er stabil.",
            kind="network", details=type(exc).__name__, retryable=False)

    # -- metadata ----------------------------------------------------------------
    def get_provider_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            id=self.id,
            name="ElevenLabs",
            description=(
                "Svært naturlige KI-stemmer fra ElevenLabs (betalt tjeneste, krever egen konto). Teksten som "
                "skal leses, sendes over internett til ElevenLabs."
            ),
            is_local=False,
            is_paid=True,
            auth_method="api_key",
            timestamps="word",
            timestamps_note=(
                "Ord for ord: ElevenLabs oppgir når hvert tegn i teksten blir sagt, og Medleser kobler "
                "tidspunktene til ordene."
            ),
            max_chars_per_request=SEGMENT_HARD_MAX,
            preferred_segment_chars=1500,
            supports_cancel=False,
            supports_speed=True,
            languages=("multi",),
            pricing_note=(
                "ElevenLabs trekker kreditter for hvert tegn som gjøres om til tale. Gratiskontoer har en liten "
                "månedlig kvote. Medleser viser alltid et overslag og ber om godkjenning før tekst sendes. "
                "Betaling skjer direkte mellom deg og ElevenLabs."
            ),
            website="https://elevenlabs.io",
        )

    def _model(self, model_id: Optional[str]) -> Optional[ModelInfo]:
        model_id = model_id or MODELS[0].id
        for model in (self._models or []):
            if model.id == model_id:
                return model
        return MODELS_BY_ID.get(model_id)

    def max_chars(self, model_id: Optional[str] = None) -> int:
        model = self._model(model_id)
        limit = model.max_chars_per_request if model and model.max_chars_per_request else SEGMENT_HARD_MAX
        return min(limit, SEGMENT_HARD_MAX)

    def list_models(self, language: Optional[str] = None) -> list[ModelInfo]:
        models = list(self._models) if self._models else list(MODELS)
        models = [m for m in models if _model_supports(m, language)]
        models.sort(key=lambda m: _MODEL_ORDER.index(m.id) if m.id in _MODEL_ORDER else len(_MODEL_ORDER))
        return models

    def _fetch_models(self, key: str) -> None:
        """Live model list (free endpoint). Silently keeps the fallback list on any problem."""
        try:
            with self._client(key, timeout=15.0) as client:
                response = client.get("/v1/models")
            if response.status_code != 200:
                return
            items = response.json()
        except (httpx.HTTPError, ValueError):
            return
        models: list[ModelInfo] = []
        for item in items if isinstance(items, list) else []:
            model_id = str(item.get("model_id") or "")
            if not model_id or not item.get("can_do_text_to_speech") or item.get("requires_alpha_access"):
                continue
            if model_id in ("eleven_turbo_v2_5", "eleven_turbo_v2"):
                continue  # deprecated – Flash replaces them
            known = MODELS_BY_ID.get(model_id)
            langs = tuple(str(lang.get("language_id", "")).lower() for lang in item.get("languages") or []
                          if lang.get("language_id"))
            rates = item.get("model_rates") or {}
            multiplier = rates.get("character_cost_multiplier")
            limit = item.get("maximum_text_length_per_request")
            models.append(ModelInfo(
                id=model_id,
                name=known.name if known else str(item.get("name") or model_id),
                description=known.description if known else (
                    "Modell fra ElevenLabs." + _TIMESTAMP_NOTE_UNCONFIRMED),
                languages=langs or (known.languages if known else ()),
                max_chars_per_request=int(limit) if isinstance(limit, int) and limit > 0 else (
                    known.max_chars_per_request if known else SEGMENT_HARD_MAX),
                credits_per_char=float(multiplier) if isinstance(multiplier, (int, float)) else (
                    known.credits_per_char if known else None),
                supports_timestamps=known.supports_timestamps if known else False,
            ))
        if models:
            with self._lock:
                self._models = models

    def status(self) -> ProviderStatus:
        if not self.secrets.has(self.id):
            return ProviderStatus("not_connected", "Ikke tilkoblet.")
        if self._last_status is not None:
            return self._last_status
        return ProviderStatus("ready", "Tilkoblet. Trykk «Test tilkobling» for å sjekke forbindelsen.",
                              account=dict(self._account))

    def forget(self) -> None:
        with self._lock:
            self._voices = None
            self._models = None
            self._account = {}
            self._last_status = None

    def check_connection(self) -> ProviderStatus:
        """Free check: list voices (and read the subscription when permitted)."""
        try:
            key = self._key()
        except TTSError:
            return ProviderStatus("not_connected", "Ikke tilkoblet.")
        try:
            voices = self._fetch_voices(key)
        except TTSError as exc:
            status = ProviderStatus("error", exc.message, exc.details, account={"error_kind": exc.kind})
            self._last_status = status
            return status
        account = self._fetch_subscription(key)
        self._fetch_models(key)
        message = f"Fant {len(voices)} stemmer."
        if account.get("remaining_credits") is not None:
            message += f" Du har omtrent {account['remaining_credits']:,} kreditter igjen denne perioden.".replace(",", " ")
        status = ProviderStatus("ready", message, account=account)
        self._last_status = status
        return status

    def _fetch_voices(self, key: str) -> list[Voice]:
        voices: list[Voice] = []
        try:
            with self._client(key) as client:
                response = client.get("/v1/voices")
                if response.status_code != 200:
                    raise self._error(response)
                payload = response.json()
        except httpx.HTTPError as exc:
            raise self._network_error(exc) from exc
        for item in payload.get("voices", []):
            voice_id = str(item.get("voice_id") or "")
            if not voice_id:
                continue
            labels = item.get("labels") or {}
            bits = [str(labels.get(k)) for k in ("gender", "accent", "age", "description", "use_case") if labels.get(k)]
            category = item.get("category")
            if category == "cloned":
                bits.insert(0, "din egen stemme")
            preview = item.get("preview_url") or ""
            voices.append(Voice(
                id=voice_id,
                name=str(item.get("name") or voice_id),
                language="multi",
                languages=("multi",),
                gender=labels.get("gender"),
                description=", ".join(bits),
                quality_note=(
                    "KI-stemme fra ElevenLabs som kan lese mange språk. Uttalen på norsk varierer mellom "
                    "stemmene – hør en prøve."
                ),
                preview_available=bool(preview),
                extra={"preview_url": preview},
            ))
        voices.sort(key=lambda v: v.name.lower())
        with self._lock:
            self._voices = (time.time(), voices)
        return voices

    def _fetch_subscription(self, key: str) -> dict[str, Any]:
        account: dict[str, Any] = {}
        try:
            with self._client(key, timeout=15.0) as client:
                response = client.get("/v1/user/subscription")
            if response.status_code == 200:
                data = response.json()
                used = data.get("character_count")
                limit = data.get("character_limit")
                account["tier"] = data.get("tier")
                if isinstance(used, (int, float)) and isinstance(limit, (int, float)):
                    account["remaining_credits"] = max(0, int(limit - used))
                    account["credit_limit"] = int(limit)
                reset = data.get("next_character_count_reset_unix")
                if reset:
                    account["resets_at"] = reset
        except (httpx.HTTPError, ValueError):
            pass  # optional information (the key may lack the user_read permission)
        with self._lock:
            self._account = account
        return account

    def list_voices(self, language: Optional[str] = None) -> list[Voice]:
        if not self.secrets.has(self.id):
            return []
        with self._lock:
            cached = self._voices
        if cached is None or time.time() - cached[0] > 600:
            try:
                return self._fetch_voices(self._key())
            except TTSError:
                return cached[1] if cached else []
        return cached[1]

    def estimate_usage(self, characters: int, model_id: Optional[str] = None) -> UsageEstimate:
        model = self._model(model_id)
        per_char = model.credits_per_char if model and model.credits_per_char is not None else None
        credits = round(characters * per_char, 1) if per_char is not None else None
        remaining = self._account.get("remaining_credits")
        return UsageEstimate(
            characters=characters,
            credits=credits,
            exact=False,
            note=(
                "Overslaget er omtrentlig. ElevenLabs bestemmer selv hvor mange kreditter som trekkes, og det "
                "kan avhenge av abonnementet ditt. Prisen i kroner avhenger av avtalen din med ElevenLabs."
            ),
            remaining_credits=float(remaining) if remaining is not None else None,
        )

    # -- previews --------------------------------------------------------------------
    def preview_audio(self, voice_id: str, cache_dir: Path) -> Path:
        voice = next((v for v in self.list_voices() if v.id == voice_id), None)
        url = (voice.extra.get("preview_url") if voice else "") or ""
        parsed = urlparse(url)
        host_ok = parsed.scheme == "https" and any(
            parsed.hostname == h or (parsed.hostname or "").endswith("." + h) for h in PREVIEW_HOSTS)
        if not voice or not host_ok:
            raise TTSError("Denne stemmen har ingen ferdig stemmeprøve.", kind="voice_not_found")
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch for ch in voice_id if ch.isalnum())[:64]
        target = cache_dir / f"elevenlabs-{safe_id}.mp3"
        if target.exists():
            return target
        try:
            with self._client(None, timeout=30.0) as client:  # public file: no key is sent
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise self._network_error(exc) from exc
        if response.status_code != 200 or len(response.content) > 5_000_000:
            raise TTSError("Stemmeprøven kunne ikke hentes.", kind="server",
                           details=f"HTTP {response.status_code}")
        target.write_bytes(response.content)
        return target

    # -- synthesis ---------------------------------------------------------------------
    def synthesize(self, request: SynthesisRequest, cancel: Optional[threading.Event] = None) -> SynthesisResult:
        if not request.text.strip():
            return SynthesisResult(np.zeros(0, dtype=np.int16), PCM_RATE, "word", [])
        if cancel is not None and cancel.is_set():
            raise TTSError("Avbrutt.", kind="cancelled")
        key = self._key()
        model_id = request.model_id or MODELS[0].id
        body: dict[str, Any] = {"text": request.text, "model_id": model_id}
        if model_id not in NO_LANGUAGE_CODE_MODELS:
            code = _language_code(request.language)
            if code:
                body["language_code"] = code
        speed = float(request.speed or 1.0)
        if abs(speed - 1.0) > 1e-3:
            body["voice_settings"] = {"speed": max(0.7, min(1.2, speed))}
        if request.previous_text:
            body["previous_text"] = request.previous_text[-500:]
        if request.next_text:
            body["next_text"] = request.next_text[:500]

        output_format = PCM_FORMAT
        for attempt in range(2):
            try:
                with self._client(key, timeout=180.0) as client:
                    response = client.post(f"/v1/text-to-speech/{quote(request.voice_id, safe='')}/with-timestamps",
                                           params={"output_format": output_format}, json=body)
            except httpx.HTTPError as exc:
                raise self._synthesis_network_error(exc) from exc
            if response.status_code == 200:
                break
            status, _ = self._detail(response)
            if attempt == 0 and response.status_code in (400, 403, 422) and "format" in (status + response.text).lower():
                output_format = FALLBACK_FORMAT  # PCM not allowed on this plan
                continue
            raise self._error(response)
        try:
            payload = response.json()
            audio_bytes = base64.b64decode(payload["audio_base64"])
        except (ValueError, KeyError) as exc:
            raise TTSError("Uventet svar fra ElevenLabs.", kind="server", details=repr(exc), retryable=True) from exc

        if output_format.startswith("pcm_"):
            pcm = np.frombuffer(audio_bytes[: len(audio_bytes) // 2 * 2], dtype="<i2").astype(np.int16)
            sample_rate = PCM_RATE
        else:
            from ..mp3 import decode_mp3_bytes

            pcm, sample_rate = decode_mp3_bytes(audio_bytes)

        words = self.word_timings(request.text, payload.get("alignment") or payload.get("normalized_alignment"))
        billed = response.headers.get("character-cost") or response.headers.get("x-character-count")
        try:
            chars_billed = int(billed) if billed is not None else len(request.text)
        except ValueError:
            chars_billed = len(request.text)
        return SynthesisResult(
            pcm=pcm,
            sample_rate=sample_rate,
            precision="word" if words else "segment",
            words=words,
            chars_billed=chars_billed,
            request_id=response.headers.get("request-id"),
        )

    @staticmethod
    def word_timings(text: str, alignment: Optional[dict[str, Any]]) -> list[WordTiming]:
        """Turn character timestamps into word timings for ``text``."""
        if not alignment:
            return []
        chars = alignment.get("characters") or []
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []
        n = min(len(chars), len(starts), len(ends))
        if n == 0:
            return []
        # Flatten (an entry may hold more than one character) and remember the owner entry.
        flat: list[str] = []
        owner: list[int] = []
        for i in range(n):
            for ch in str(chars[i]):
                flat.append(ch)
                owner.append(i)
        aligned_text = "".join(flat)
        # Map each text index to an alignment entry.
        if aligned_text == text:
            index_map: list[Optional[int]] = list(owner)
        else:
            index_map = [None] * len(text)
            matcher = difflib.SequenceMatcher(None, text, aligned_text, autojunk=False)
            for block in matcher.get_matching_blocks():
                for k in range(block.size):
                    index_map[block.a + k] = owner[block.b + k]
        words: list[WordTiming] = []
        for m in WORD_RE.finditer(text):
            idxs = [index_map[i] for i in range(m.start(), m.end()) if index_map[i] is not None]
            if not idxs:
                continue
            t0 = float(starts[min(idxs)])
            t1 = float(ends[max(idxs)])
            words.append(WordTiming(m.start(), m.end(), t0, max(t0, t1)))
        return words
