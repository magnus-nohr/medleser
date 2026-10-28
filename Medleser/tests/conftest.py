"""Shared test fixtures.

Paid API calls are never made in the default test run: the ElevenLabs tests
use a mocked HTTP transport, and the real integration test is marked
``integration`` (excluded by default in pyproject.toml).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from medleser.runtime_fix import preload_cpp_runtime  # noqa: E402

preload_cpp_runtime()  # same DLL order as the app (see medleser/runtime_fix.py)

from medleser.config import AppPaths  # noqa: E402
from medleser.speech.providers.base import (  # noqa: E402
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
from medleser.speech.providers.piper_align import WORD_RE  # noqa: E402


class MemoryKeyring:
    """In-memory keyring backend so tests never touch Credential Manager."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture
def memory_keyring(monkeypatch):
    import keyring

    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


@pytest.fixture
def app_paths(tmp_path, monkeypatch) -> AppPaths:
    root = tmp_path / "data"
    monkeypatch.setenv("MEDLESER_DATA_DIR", str(root))
    return AppPaths(root).ensure()


class FakeProvider(TTSProvider):
    """Deterministic test double: 0.1 s tone per word, exact word timings."""

    id = "fake"

    def __init__(self, *, paid: bool = False, precision: str = "word", sample_rate: int = 16000,
                 fail_at: Optional[int] = None, fail_kind: str = "server", retryable: bool = False) -> None:
        self.paid = paid
        self.precision = precision
        self.sample_rate = sample_rate
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.fail_kind = fail_kind
        self.retryable = retryable

    def get_provider_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            id=self.id, name="Testtjeneste", description="", is_local=not self.paid, is_paid=self.paid,
            auth_method="none", timestamps="word", timestamps_note="Ord for ord (test).",
            max_chars_per_request=1000, preferred_segment_chars=200, supports_cancel=True,
            supports_speed=True, languages=("nb", "en"), pricing_note="",
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus("ready", "Klar")

    def check_connection(self) -> ProviderStatus:
        return self.status()

    def list_voices(self, language=None):
        return [Voice(id="v1", name="Teststemme", language="nb", languages=("nb", "en"))]

    def estimate_usage(self, characters, model_id=None):
        if self.paid:
            return UsageEstimate(characters, float(characters), False, "Omtrentlig.")
        return super().estimate_usage(characters, model_id)

    def synthesize(self, request: SynthesisRequest, cancel: Optional[threading.Event] = None) -> SynthesisResult:
        self.calls.append(request.text)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise TTSError("Testfeil", kind=self.fail_kind, retryable=self.retryable)
        words = [(m.start(), m.end()) for m in WORD_RE.finditer(request.text)]
        per_word = int(0.1 * self.sample_rate)
        t = np.arange(per_word) / self.sample_rate
        tone = (0.2 * 32767 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
        pcm = np.concatenate([tone for _ in words]) if words else np.zeros(0, dtype=np.int16)
        timings = [WordTiming(s, e, k * 0.1, (k + 1) * 0.1) for k, (s, e) in enumerate(words)]
        if self.precision != "word":
            timings = []
        return SynthesisResult(pcm=pcm, sample_rate=self.sample_rate,
                               precision="word" if timings else "segment", words=timings,
                               chars_billed=len(request.text) if self.paid else 0)


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()
