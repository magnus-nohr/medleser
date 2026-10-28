"""Registry of speech providers.

To add a provider (e.g. Google Cloud TTS or Azure Speech): implement
``TTSProvider`` in a new module and register it in ``ProviderRegistry``.
Only providers with a working integration are registered, so the UI never
offers something that does not work.
"""

from __future__ import annotations

import logging
from typing import Optional

from ...config import AppPaths
from .base import TTSError, TTSProvider

_LOG = logging.getLogger(__name__)


class ProviderRegistry:
    def __init__(self, paths: AppPaths, secrets, *, http_transport=None) -> None:
        from .piper_provider import PiperProvider
        from .windows_provider import WindowsVoiceProvider

        self._providers: dict[str, TTSProvider] = {}
        self.register(PiperProvider(paths.piper_voices))
        self.register(WindowsVoiceProvider())
        try:
            from .elevenlabs_provider import ElevenLabsProvider

            self.register(ElevenLabsProvider(secrets, transport=http_transport))
        except ImportError as exc:  # module not available in this build
            _LOG.warning("ElevenLabs provider not available: %s", exc)

    def register(self, provider: TTSProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: Optional[str]) -> TTSProvider:
        provider = self._providers.get(provider_id or "")
        if provider is None:
            raise TTSError("Ukjent stemmetjeneste.", kind="unavailable")
        return provider

    def all(self) -> list[TTSProvider]:
        return list(self._providers.values())

    def close(self) -> None:
        for provider in self._providers.values():
            try:
                provider.close()
            except Exception:  # noqa: BLE001
                pass
