"""Common interface for text-to-speech providers.

Every provider (local Piper, built-in Windows voices, ElevenLabs, and later
Google/Azure) implements :class:`TTSProvider`. The rest of the app only talks
to this interface, so adding a provider never requires changes elsewhere
beyond registering it in ``providers/__init__.py``.

Audio contract: ``synthesize`` returns mono signed 16-bit PCM as a numpy
``int16`` array plus its sample rate. Timing contract: word timings refer to
character offsets in the exact ``SynthesisRequest.text`` that was sent and to
seconds relative to the start of the returned audio. Providers must only
return timings that come from the engine/service itself (never estimated
from word length).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

import numpy as np

from ...errors import UserFacingError

TimingPrecision = Literal["word", "sentence", "segment"]


@dataclass(frozen=True)
class ModelInfo:
    """A synthesis model offered by a provider (ElevenLabs has several)."""

    id: str
    name: str
    description: str = ""
    languages: tuple[str, ...] = ()
    max_chars_per_request: int = 0
    credits_per_char: Optional[float] = None  # None = unknown
    supports_timestamps: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["languages"] = list(self.languages)
        return d


@dataclass(frozen=True)
class Voice:
    id: str  # provider-specific id
    name: str  # display name (Norwegian where we control it)
    language: str  # primary language code ("nb", "en", … or "multi")
    languages: tuple[str, ...] = ()  # all languages the voice can speak
    gender: Optional[str] = None  # "female" | "male" | None
    description: str = ""
    quality_note: str = ""  # honest note about quality/evaluation status
    preview_available: bool = False  # a free, pre-recorded preview exists
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def speaks(self, language: str) -> bool:
        base = language.split("-")[0].lower()
        langs = {self.language.lower(), *(lang.lower() for lang in self.languages)}
        if "multi" in langs:
            return True
        if base in ("nb", "nn", "no"):
            return bool(langs & {"nb", "nn", "no"})
        return base in langs

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "languages": list(self.languages),
            "gender": self.gender,
            "description": self.description,
            "quality_note": self.quality_note,
            "preview_available": self.preview_available,
        }


@dataclass(frozen=True)
class ProviderCapabilities:
    id: str
    name: str  # Norwegian display name
    description: str  # Norwegian, plain language
    is_local: bool  # True: text never leaves this computer
    is_paid: bool  # True: generation may consume paid credits
    auth_method: Literal["none", "api_key"]
    timestamps: TimingPrecision  # best timing precision the provider delivers
    timestamps_note: str  # Norwegian explanation of the highlighting precision
    max_chars_per_request: int
    preferred_segment_chars: int
    supports_cancel: bool
    supports_speed: bool
    languages: tuple[str, ...]
    pricing_note: str  # Norwegian: free / paid explanation
    website: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["languages"] = list(self.languages)
        return d


@dataclass
class ProviderStatus:
    state: Literal["ready", "not_connected", "needs_setup", "error", "unavailable"]
    message: str  # Norwegian
    details: Optional[str] = None
    account: dict[str, Any] = field(default_factory=dict)  # e.g. remaining credits

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SynthesisRequest:
    text: str
    voice_id: str
    language: str
    model_id: Optional[str] = None
    speed: float = 1.0  # 1.0 = normal speaking rate
    previous_text: Optional[str] = None  # context for natural prosody (cloud)
    next_text: Optional[str] = None
    previous_request_ids: tuple[str, ...] = ()


@dataclass
class WordTiming:
    start_char: int  # offset into SynthesisRequest.text
    end_char: int  # exclusive
    start: float  # seconds from the start of the returned audio
    end: float


@dataclass
class SynthesisResult:
    pcm: np.ndarray  # int16 mono
    sample_rate: int
    precision: TimingPrecision
    words: list[WordTiming] = field(default_factory=list)
    chars_billed: int = 0  # characters reported/charged by a paid service
    request_id: Optional[str] = None

    @property
    def duration(self) -> float:
        return len(self.pcm) / float(self.sample_rate) if self.sample_rate else 0.0


@dataclass
class UsageEstimate:
    characters: int
    credits: Optional[float]  # None when it cannot be estimated reliably
    exact: bool
    note: str  # Norwegian explanation shown to the user
    remaining_credits: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TTSError(UserFacingError):
    """Synthesis/connection failure with a Norwegian message.

    ``kind`` lets the generator decide what to do: retry (rate_limit,
    server, network), stop the job (auth, quota, invalid_request) or ask the
    user to act.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Literal[
            "auth",
            "permission",
            "quota",
            "rate_limit",
            "network",
            "server",
            "invalid_request",
            "voice_not_found",
            "unavailable",
            "cancelled",
            "spending_limit",
        ],
        details: Optional[str] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code=f"tts_{kind}", details=details, status=502 if retryable else 400)
        self.kind = kind
        self.retryable = retryable


class TTSProvider(ABC):
    """Base class for all speech providers."""

    id: str = ""

    @abstractmethod
    def get_provider_capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def status(self) -> ProviderStatus:
        """Cheap, offline-safe status (no network calls)."""

    @abstractmethod
    def list_voices(self, language: Optional[str] = None) -> list[Voice]: ...

    def list_models(self, language: Optional[str] = None) -> list[ModelInfo]:
        """Models that can read ``language`` (all models when None)."""
        return []

    @abstractmethod
    def check_connection(self) -> ProviderStatus:
        """Verify that the provider works. Must never consume paid credits."""

    @abstractmethod
    def synthesize(
        self, request: SynthesisRequest, cancel: Optional[threading.Event] = None
    ) -> SynthesisResult:
        """Generate audio (with authentic timestamps when the provider has them)."""

    def estimate_usage(self, characters: int, model_id: Optional[str] = None) -> UsageEstimate:
        return UsageEstimate(
            characters=characters,
            credits=0.0,
            exact=True,
            note="Gratis: lyden lages på denne datamaskinen.",
        )

    def max_chars(self, model_id: Optional[str] = None) -> int:
        return self.get_provider_capabilities().max_chars_per_request

    def close(self) -> None:
        """Release resources (models, HTTP clients)."""
