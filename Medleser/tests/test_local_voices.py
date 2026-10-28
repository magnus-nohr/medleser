"""Real local speech synthesis (Piper and Windows voices) end to end.

Marked ``slow``/``windows``; skipped automatically when a voice is missing.
"""

import json
import sys

import pytest

from medleser.documents.model import Block, Document
from medleser.jobs import Job
from medleser.speech.generator import AudioService
from medleser.speech.mp3 import probe_mp3
from medleser.speech.providers import piper_catalog
from medleser.speech.providers.base import SynthesisRequest
from medleser.speech.providers.piper_provider import PiperProvider
from medleser.speech.providers.windows_provider import WindowsVoiceProvider
from medleser.storage.db import Database
from medleser.storage.projects import ProjectStore

def real_voice_root():
    import os
    from pathlib import Path

    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Medleser" / "voices" / "piper" if base else None


NB_VOICE = "no_NO-nvcc-medium"


@pytest.fixture(scope="module")
def piper():
    root = real_voice_root()
    if root is None or not piper_catalog.is_installed(root, NB_VOICE):
        pytest.skip("Norsk Piper-stemme er ikke installert")
    return PiperProvider(root)


def test_piper_rejects_voice_ids_outside_the_catalog(tmp_path):
    from medleser.speech.providers.base import TTSError

    provider = PiperProvider(tmp_path)
    for bad in ("..\\..\\evil", "../x#KNN", "unknown-voice"):
        assert not piper_catalog.is_installed(tmp_path, bad.split("#")[0])
        with pytest.raises(TTSError):
            provider.synthesize(SynthesisRequest(text="Hei.", voice_id=bad, language="nb"))


@pytest.mark.slow
def test_piper_word_timings_cover_all_words(piper):
    text = "Studenter med dysleksi kan lytte til pensum. Medleser markerer hvert ord, også tall som 2026."
    result = piper.synthesize(SynthesisRequest(text=text, voice_id=f"{NB_VOICE}#KNN", language="nb"))
    assert result.precision == "word"
    spoken = [text[w.start_char:w.end_char] for w in result.words]
    assert spoken == ["Studenter", "med", "dysleksi", "kan", "lytte", "til", "pensum", "Medleser", "markerer",
                      "hvert", "ord", "også", "tall", "som", "2026"]
    starts = [w.start for w in result.words]
    assert starts == sorted(starts)
    assert result.words[-1].end <= result.duration + 0.01
    assert result.duration > 3.0


@pytest.mark.slow
def test_piper_end_to_end_document_to_mp3(piper, app_paths, tmp_path):
    db = Database(app_paths.db)
    store = ProjectStore(app_paths.projects, db)
    src = tmp_path / "pensum.txt"
    src.write_text("x", encoding="utf-8")
    pid = store.create(src, "pensum.txt", 1)
    from medleser.documents.cleanup import detect_spans

    url_text = "Vi intervjuet 12 studenter (se https://example.org/rapport) i 2025."
    blocks = [Block("b00000", "heading", "Innledning", level=1),
              Block("b00001", "paragraph", "Lesing er en sammensatt ferdighet. Mange studenter bruker lydbøker, f.eks. til pensum."),
              Block("b00002", "heading", "Metode", level=1),
              Block("b00003", "paragraph", url_text, spans=detect_spans(url_text))]
    assert any(s.kind == "url" for s in blocks[3].spans)
    store.save_document(pid, Document(title="Pensum", source_name="pensum.txt", source_type="txt",
                                      size_bytes=1, blocks=blocks, language="nb"))
    store.update_settings(pid, provider="piper", voice_id=f"{NB_VOICE}#KNN", language="nb")

    class Registry:
        def get(self, _):
            return piper

    service = AudioService(store, db, Registry())
    result = service.generate(Job("j", "generate", pid), pid)
    assert result["precision"] == "word"
    info = probe_mp3(store.audio_path(pid))
    assert info.ok and info.duration > 5
    sync = json.loads(store.sync_path(pid).read_text(encoding="utf-8"))
    doc = store.load_document(pid)
    shown = [doc.block_by_id(b).text[ds:de] for _, _, _, b, _, ds, de in sync["words"]]
    assert "f.eks." in shown  # expanded for speech, highlighted as written
    assert not any("example" in s for s in shown)  # URL was skipped
    assert [c["title"] for c in sync["chapters"]] == ["Innledning", "Metode"]
    db.close()


@pytest.mark.slow
@pytest.mark.windows
def test_windows_voice_word_timings():
    if sys.platform != "win32":
        pytest.skip("Bare Windows")
    provider = WindowsVoiceProvider()
    voices = provider.list_voices("nb")
    if not voices:
        pytest.skip("Ingen norsk Windows-stemme installert")
    text = "Dette er en test av Windows-stemmen. Ordene får egne tidspunkter."
    result = provider.synthesize(SynthesisRequest(text=text, voice_id=voices[0].id, language="nb"))
    assert result.precision == "word"
    spoken = [text[w.start_char:w.end_char] for w in result.words]
    assert spoken[:6] == ["Dette", "er", "en", "test", "av", "Windows-stemmen"]
    assert all(b.start >= a.start for a, b in zip(result.words, result.words[1:]))


@pytest.mark.slow
@pytest.mark.windows
def test_windows_voice_from_worker_thread():
    """Synthesis runs in background threads in the app (COM apartment handling)."""
    if sys.platform != "win32":
        pytest.skip("Bare Windows")
    import threading

    provider = WindowsVoiceProvider()
    voices = provider.list_voices()
    if not voices:
        pytest.skip("Ingen Windows-stemmer")
    out = {}

    def work():
        try:
            out["r"] = provider.synthesize(SynthesisRequest(text="Hei fra en tråd.", voice_id=voices[0].id, language="nb"))
        except Exception as exc:  # noqa: BLE001
            out["e"] = exc

    t = threading.Thread(target=work)
    t.start()
    t.join(30)
    assert "e" not in out, out.get("e")
    assert out["r"].duration > 0.3
