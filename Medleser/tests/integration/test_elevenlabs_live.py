"""Live tests against the real ElevenLabs API – OFF by default.

They only run when explicitly enabled:

    $env:MEDLESER_ELEVENLABS_LIVE = "1"            # free checks (connection, voices, models)
    $env:MEDLESER_ALLOW_PAID_TESTS = "1"           # ALSO the paid test (about 20 characters)
    .venv\\Scripts\\python.exe -m pytest -m integration tests/integration -q

The API key is read from Windows Credential Manager (saved by Medleser's
connection wizard) or from MEDLESER_ELEVENLABS_KEY.
"""

import os

import pytest

from medleser.speech.providers.base import SynthesisRequest
from medleser.speech.providers.elevenlabs_provider import ElevenLabsProvider
from medleser.storage.secrets import SecretStore

pytestmark = pytest.mark.integration


class _EnvOrKeyring(SecretStore):
    def get(self, provider_id):
        return os.environ.get("MEDLESER_ELEVENLABS_KEY") or super().get(provider_id)


@pytest.fixture(scope="module")
def live_provider():
    if os.environ.get("MEDLESER_ELEVENLABS_LIVE") != "1":
        pytest.skip("Live-test er ikke slått på (MEDLESER_ELEVENLABS_LIVE=1)")
    provider = ElevenLabsProvider(_EnvOrKeyring())
    if not provider.secrets.get("elevenlabs"):
        pytest.skip("Ingen ElevenLabs-nøkkel funnet")
    return provider


def test_live_connection_is_free_and_lists_voices(live_provider):
    status = live_provider.check_connection()
    assert status.state == "ready", status.message
    assert live_provider.list_voices()
    assert live_provider.list_models("nb"), "ingen modell for norsk"


def test_live_paid_synthesis_with_timestamps(live_provider):
    if os.environ.get("MEDLESER_ALLOW_PAID_TESTS") != "1":
        pytest.skip("Betalt test er ikke godkjent (MEDLESER_ALLOW_PAID_TESTS=1)")
    voice = live_provider.list_voices()[0]
    text = "Hei, dette er en kort test."  # 27 characters
    result = live_provider.synthesize(SynthesisRequest(text=text, voice_id=voice.id, language="nb",
                                                       model_id="eleven_flash_v2_5"))
    assert result.duration > 0.5
    assert result.precision == "word"
    assert [text[w.start_char:w.end_char] for w in result.words][:2] == ["Hei", "dette"]
