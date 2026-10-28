"""ElevenLabs integration tests against a simulated API (no real calls, no credits)."""

import base64
import json
import logging

import httpx
import numpy as np
import pytest

from medleser.speech.providers.base import SynthesisRequest, TTSError
from medleser.speech.providers.elevenlabs_provider import ElevenLabsProvider
from medleser.storage.secrets import SecretStore

KEY = "sk_" + "a1b2c3d4" * 6


def alignment_for(text, per_char=0.05):
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * per_char, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * per_char, 3) for i in range(len(text))],
    }


class FakeElevenLabs:
    """Minimal stand-in for the ElevenLabs REST API."""

    def __init__(self):
        self.valid_key = KEY
        self.requests = []
        self.mode = "ok"
        self.subscription_status = 200
        self.preview_url = "https://storage.googleapis.com/eleven-public-prod/premade/voices/abc/preview.mp3"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "storage.googleapis.com":
            assert "xi-api-key" not in request.headers  # the key never goes to other hosts
            return httpx.Response(200, content=b"ID3fake-mp3")
        if request.headers.get("xi-api-key") != self.valid_key:
            return httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
        if path == "/v1/voices":
            return httpx.Response(200, json={"voices": [
                {"voice_id": "voice123", "name": "Aria", "category": "premade",
                 "labels": {"gender": "female", "accent": "american"}, "preview_url": self.preview_url},
                {"voice_id": "voice456", "name": "Bjørn", "category": "professional", "labels": {}, "preview_url": ""},
            ]})
        if path == "/v1/models":
            if self.mode == "no_models_permission":
                return httpx.Response(403, json={"detail": {"status": "missing_permissions", "message": "models_read"}})
            return httpx.Response(200, json=[
                {"model_id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5", "can_do_text_to_speech": True,
                 "maximum_text_length_per_request": 40000, "model_rates": {"character_cost_multiplier": 0.5},
                 "languages": [{"language_id": "en", "name": "English"}, {"language_id": "no", "name": "Norwegian"}]},
                {"model_id": "eleven_multilingual_v2", "name": "Eleven Multilingual v2", "can_do_text_to_speech": True,
                 "maximum_text_length_per_request": 10000, "model_rates": {"character_cost_multiplier": 1.0},
                 "languages": [{"language_id": "en", "name": "English"}, {"language_id": "sv", "name": "Swedish"}]},
                {"model_id": "eleven_v4", "name": "Eleven v4", "can_do_text_to_speech": True,
                 "maximum_text_length_per_request": 10000, "model_rates": {"character_cost_multiplier": 1.0},
                 "languages": [{"language_id": "nob", "name": "Norwegian Bokmål"}, {"language_id": "en", "name": "English"}]},
                {"model_id": "eleven_turbo_v2_5", "name": "Turbo", "can_do_text_to_speech": True, "languages": []},
                {"model_id": "eleven_secret", "name": "Alpha", "can_do_text_to_speech": True, "requires_alpha_access": True},
                {"model_id": "eleven_multilingual_sts_v2", "name": "STS", "can_do_text_to_speech": False},
            ])
        if path == "/v1/user/subscription":
            if self.subscription_status != 200:
                return httpx.Response(self.subscription_status, json={"detail": {"status": "missing_permissions", "message": "user_read"}})
            return httpx.Response(200, json={"tier": "starter", "character_count": 12000, "character_limit": 30000})
        if path.startswith("/v1/text-to-speech/") and path.endswith("/with-timestamps"):
            if self.mode == "quota":
                return httpx.Response(401, json={"detail": {"status": "quota_exceeded", "message": "quota"}})
            if self.mode == "busy":
                return httpx.Response(429, json={"detail": {"status": "too_many_concurrent_requests", "message": "busy"}})
            if self.mode == "no_pcm" and request.url.params.get("output_format", "").startswith("pcm"):
                return httpx.Response(403, json={"detail": {"status": "output_format_not_allowed", "message": "output_format pcm_24000 requires a higher tier"}})
            body = json.loads(request.content)
            text = body["text"]
            fmt = request.url.params.get("output_format")
            if fmt.startswith("pcm"):
                audio = (np.zeros(int(len(text) * 0.05 * 24000), dtype="<i2")).tobytes()
            else:
                audio = make_mp3(len(text) * 0.05)
            return httpx.Response(200, headers={"request-id": "req-1", "character-cost": str(len(text))},
                                  json={"audio_base64": base64.b64encode(audio).decode(),
                                        "alignment": alignment_for(text),
                                        "normalized_alignment": alignment_for(text)})
        return httpx.Response(404, json={"detail": "not found"})


def make_mp3(seconds):
    import lameenc

    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(44100)
    enc.set_channels(1)
    pcm = (np.sin(np.arange(int(seconds * 44100)) / 10) * 3000).astype("<i2").tobytes()
    return enc.encode(pcm) + enc.flush()


@pytest.fixture
def fake():
    return FakeElevenLabs()


@pytest.fixture
def provider(fake, memory_keyring):
    secrets = SecretStore()
    return ElevenLabsProvider(secrets, transport=httpx.MockTransport(fake.handler)), secrets


def test_not_connected_without_key(provider):
    p, _ = provider
    assert p.status().state == "not_connected"
    assert p.check_connection().state == "not_connected"
    assert p.list_voices() == []


def test_connection_test_lists_voices_and_credits(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    status = p.check_connection()
    assert status.state == "ready"
    assert status.account["remaining_credits"] == 18000
    assert [v.name for v in p.list_voices("nb")] == ["Aria", "Bjørn"]
    # Only free endpoints were used.
    assert {r.url.path for r in fake.requests} == {"/v1/voices", "/v1/user/subscription", "/v1/models"}


def test_models_are_filtered_by_language_and_use_live_rates(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    p.check_connection()
    norwegian = [m.id for m in p.list_models("nb")]
    assert norwegian == ["eleven_flash_v2_5", "eleven_v4"]  # multilingual v2 cannot read Norwegian
    assert "eleven_turbo_v2_5" not in [m.id for m in p.list_models()]  # deprecated
    assert "eleven_secret" not in [m.id for m in p.list_models()]  # alpha only
    assert [m.id for m in p.list_models("en")][:2] == ["eleven_flash_v2_5", "eleven_v4"]
    assert p.estimate_usage(1000, "eleven_flash_v2_5").credits == 500
    assert p.estimate_usage(1000, "eleven_v4").credits == 1000  # live multiplier
    assert p.max_chars("eleven_v4") == 4800  # capped per request to keep retries cheap


def test_fallback_models_when_models_endpoint_is_not_permitted(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    fake.mode = "no_models_permission"
    assert p.check_connection().state == "ready"
    assert [m.id for m in p.list_models("nb")] == ["eleven_flash_v2_5", "eleven_v4", "eleven_v3"]
    assert p.estimate_usage(100, "eleven_v4").credits is None  # unknown -> not guessed


def test_language_code_is_not_sent_to_multilingual_v2(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    p.synthesize(SynthesisRequest(text="Hello there.", voice_id="voice123", language="en", model_id="eleven_multilingual_v2"))
    assert "language_code" not in json.loads(fake.requests[-1].content)


def test_missing_permission_is_explained(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)

    def forbidden(request):
        return httpx.Response(403, json={"detail": {"status": "missing_permissions", "message": "text_to_speech"}})

    p._transport = httpx.MockTransport(forbidden)
    with pytest.raises(TTSError) as err:
        p.synthesize(SynthesisRequest(text="Tekst.", voice_id="voice123", language="nb"))
    assert err.value.kind == "permission" and "Text to Speech" in err.value.message


def test_subscription_permission_is_optional(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    fake.subscription_status = 401
    status = p.check_connection()
    assert status.state == "ready" and "remaining_credits" not in status.account


def test_invalid_key_is_reported_in_plain_norwegian(provider):
    p, secrets = provider
    secrets.set("elevenlabs", "sk_wrong")
    status = p.check_connection()
    assert status.state == "error" and status.account["error_kind"] == "auth"
    assert "godtok ikke tilgangsnøkkelen" in status.message


def test_synthesize_maps_character_timestamps_to_words(provider, fake, caplog):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    caplog.set_level(logging.DEBUG)
    text = "Hei, dette er en test."
    result = p.synthesize(SynthesisRequest(text=text, voice_id="voice123", language="nb",
                                           model_id="eleven_flash_v2_5", previous_text="Forrige setning."))
    assert result.precision == "word" and result.sample_rate == 24000
    assert [text[w.start_char:w.end_char] for w in result.words] == ["Hei", "dette", "er", "en", "test"]
    first = result.words[1]
    assert first.start == pytest.approx(5 * 0.05) and first.end == pytest.approx(10 * 0.05)
    assert result.chars_billed == len(text) and result.request_id == "req-1"
    sent = json.loads(fake.requests[-1].content)
    assert sent["model_id"] == "eleven_flash_v2_5" and sent["language_code"] == "no"
    assert sent["previous_text"] == "Forrige setning."
    assert fake.requests[-1].url.params["output_format"] == "pcm_24000"
    assert KEY not in caplog.text


def test_mp3_fallback_when_pcm_is_not_allowed(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    fake.mode = "no_pcm"
    result = p.synthesize(SynthesisRequest(text="Litt tekst her.", voice_id="voice123", language="nb"))
    assert result.sample_rate == 44100 and len(result.pcm) > 0
    assert fake.requests[-1].url.params["output_format"] == "mp3_44100_128"


def test_quota_and_rate_limit_errors(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    fake.mode = "quota"
    with pytest.raises(TTSError) as err:
        p.synthesize(SynthesisRequest(text="Tekst.", voice_id="voice123", language="nb"))
    assert err.value.kind == "quota" and not err.value.retryable
    assert "kreditter" in err.value.message
    fake.mode = "busy"
    with pytest.raises(TTSError) as err:
        p.synthesize(SynthesisRequest(text="Tekst.", voice_id="voice123", language="nb"))
    assert err.value.kind == "rate_limit" and err.value.retryable


def test_missing_internet(memory_keyring):
    def offline(request):
        raise httpx.ConnectError("no route to host")

    secrets = SecretStore()
    secrets.set("elevenlabs", KEY)
    p = ElevenLabsProvider(secrets, transport=httpx.MockTransport(offline))
    with pytest.raises(TTSError) as err:
        p.synthesize(SynthesisRequest(text="Tekst.", voice_id="voice123", language="nb"))
    assert err.value.kind == "network" and err.value.retryable
    status = p.check_connection()
    assert status.state == "error" and "internett" in status.message


def test_paid_request_is_not_retried_after_a_read_timeout(memory_keyring):
    """The request may already have been processed (and charged) – never repeat it automatically."""
    def slow(request):
        raise httpx.ReadTimeout("no answer")

    secrets = SecretStore()
    secrets.set("elevenlabs", KEY)
    p = ElevenLabsProvider(secrets, transport=httpx.MockTransport(slow))
    with pytest.raises(TTSError) as err:
        p.synthesize(SynthesisRequest(text="Tekst.", voice_id="voice123", language="nb"))
    assert err.value.kind == "network" and not err.value.retryable
    assert "kan ha trukket kreditter" in err.value.message


def test_voice_id_is_escaped_in_the_url(provider, fake):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    p.synthesize(SynthesisRequest(text="Tekst.", voice_id="../../v1/user", language="nb"))
    raw = fake.requests[-1].url.raw_path.decode()
    # The id stays one encoded path segment of the text-to-speech endpoint.
    assert raw.startswith("/v1/text-to-speech/..%2F..%2Fv1%2Fuser/with-timestamps")


def test_preview_uses_allow_listed_host_without_key(provider, fake, tmp_path):
    p, secrets = provider
    secrets.set("elevenlabs", KEY)
    p.check_connection()
    path = p.preview_audio("voice123", tmp_path)
    assert path.read_bytes() == b"ID3fake-mp3"
    with pytest.raises(TTSError):
        p.preview_audio("voice456", tmp_path)  # no preview
    fake.preview_url = "https://evil.example.com/x.mp3"
    p.forget()
    p.check_connection()
    p._voices = None
    with pytest.raises(TTSError):
        p.preview_audio("voice123", tmp_path / "other")


def test_word_timings_tolerate_normalized_differences():
    text = "Det kostet 50 % mer."
    aligned = "Det kostet 50 prosent mer."
    alignment = alignment_for(aligned)
    words = ElevenLabsProvider.word_timings(text, alignment)
    assert [text[w.start_char:w.end_char] for w in words] == ["Det", "kostet", "50", "mer"]
    assert all(w.end >= w.start for w in words)
