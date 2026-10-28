import json
import struct
from pathlib import Path

import pytest

from medleser.documents.model import Block, Document
from medleser.errors import CancelledError, UserFacingError
from medleser.jobs import Job
from medleser.speech.generator import AudioService
from medleser.speech.mp3 import Mp3Tags, Mp3Writer, id3v2_tag, probe_mp3
from medleser.storage.db import Database
from medleser.storage.projects import ProjectStore, safe_filename

from .conftest import FakeProvider


class Registry:
    def __init__(self, provider):
        self.provider = provider

    def get(self, provider_id):
        return self.provider

    def all(self):
        return [self.provider]


def make_document(n_paragraphs=6):
    blocks = [Block("b00000", "heading", "Innledning", level=2)]
    for i in range(n_paragraphs):
        if i == n_paragraphs // 2:
            blocks.append(Block(f"b{len(blocks):05d}", "heading", "Metode", level=2))
        blocks.append(Block(f"b{len(blocks):05d}", "paragraph",
                            f"Dette er avsnitt nummer {i + 1}. Det har to setninger med flere ord."))
    return Document(title="Testdokument", source_name="test.txt", source_type="txt", size_bytes=10,
                    blocks=blocks, language="nb")


@pytest.fixture
def env(app_paths, tmp_path):
    db = Database(app_paths.db)
    store = ProjectStore(app_paths.projects, db)
    src = tmp_path / "test.txt"
    src.write_text("hei", encoding="utf-8")
    pid = store.create(src, "test.txt", 3)
    store.save_document(pid, make_document())
    store.update_settings(pid, provider="fake", voice_id="v1", language="nb")
    yield db, store, pid
    db.close()


def new_job():
    return Job(id="j", kind="generate", project_id=None)


def test_generate_creates_mp3_and_word_sync(env):
    db, store, pid = env
    provider = FakeProvider()
    service = AudioService(store, db, Registry(provider))
    result = service.generate(new_job(), pid)
    assert result["precision"] == "word"
    mp3 = store.audio_path(pid)
    info = probe_mp3(mp3)
    assert info.ok and info.sample_rate == 16000
    sync = json.loads(store.sync_path(pid).read_text(encoding="utf-8"))
    assert abs(sync["duration"] - info.duration) < 0.2
    assert sync["precision"] == "word"
    assert len(sync["chapters"]) == 2 and sync["chapters"][0]["title"] == "Innledning"
    assert sync["audio_offset"] == pytest.approx(1105 / 16000, abs=1e-4)  # LAME delay, measured in WebView2
    doc = store.load_document(pid)
    # Every word timing points at the exact word on screen, and times increase.
    last = -1.0
    for t0, t1, seg, block_id, part, ds, de in sync["words"]:
        text = doc.block_by_id(block_id).text
        assert text[ds:de].isalnum()
        assert t0 >= last and t1 >= t0
        last = t0
    assert db.get_project(pid)["audio_state"] == "ready"


def test_resume_only_generates_missing_segments(env):
    db, store, pid = env
    failing = FakeProvider(fail_at=3)
    service = AudioService(store, db, Registry(failing))
    with pytest.raises(UserFacingError):
        service.generate(new_job(), pid)
    assert len(failing.calls) == 3  # two segments saved before the failure
    provider = FakeProvider()
    service = AudioService(store, db, Registry(provider))
    state = service.audio_state(pid)
    assert state["cached_segments"] == 2 and not state["ready"]
    service.generate(new_job(), pid)
    assert len(provider.calls) == state["total_segments"] - 2
    # Nothing is regenerated when everything is cached.
    provider.calls.clear()
    service.generate(new_job(), pid)
    assert provider.calls == []


def test_cancel_keeps_finished_segments(env):
    db, store, pid = env
    provider = FakeProvider()
    service = AudioService(store, db, Registry(provider))
    job = new_job()
    original = provider.synthesize

    def cancel_after_two(request, cancel=None):
        if len(provider.calls) == 2:
            job.cancel_event.set()
        return original(request, cancel)

    provider.synthesize = cancel_after_two
    with pytest.raises(CancelledError):
        service.generate(job, pid)
    assert service.audio_state(pid)["cached_segments"] == 3


def test_retryable_errors_are_retried(env, monkeypatch):
    db, store, pid = env
    provider = FakeProvider(fail_at=1, fail_kind="rate_limit", retryable=True)
    service = AudioService(store, db, Registry(provider))
    job = new_job()
    monkeypatch.setattr(job.cancel_event, "wait", lambda timeout=None: False)
    service.generate(job, pid)
    assert service.audio_state(pid)["ready"]


def test_paid_provider_requires_confirmation_and_respects_limit(env):
    db, store, pid = env
    provider = FakeProvider(paid=True)
    service = AudioService(store, db, Registry(provider))
    with pytest.raises(UserFacingError) as err:
        service.generate(new_job(), pid)
    assert err.value.code == "confirmation_required"
    assert provider.calls == []  # nothing was sent without approval

    estimate = service.estimate(pid)
    assert estimate["provider"]["is_paid"] and estimate["characters_pending"] > 0
    assert estimate["usage"]["exact"] is False

    # A limit below the job size blocks the job before anything is sent.
    store.update_settings(pid, max_cloud_chars=10)
    with pytest.raises(UserFacingError) as err:
        service.generate(new_job(), pid, estimate["confirmation_token"])
    assert err.value.code == "spending_limit" and provider.calls == []

    store.update_settings(pid, max_cloud_chars=None)
    service.generate(new_job(), pid, estimate["confirmation_token"])
    assert db.usage_for_project(pid, "fake") == estimate["characters_pending"]

    # A changed text invalidates the old token.
    doc = store.load_document(pid)
    doc.blocks[1].text = "Endret tekst som ikke er godkjent."
    store.save_document(pid, doc)
    with pytest.raises(UserFacingError) as err:
        service.generate(new_job(), pid, estimate["confirmation_token"])
    assert err.value.code == "confirmation_required"
    assert service.audio_state(pid)["stale"] is True


def test_segment_level_fallback_is_reported(env):
    db, store, pid = env
    service = AudioService(store, db, Registry(FakeProvider(precision="segment")))
    service.generate(new_job(), pid)
    sync = json.loads(store.sync_path(pid).read_text(encoding="utf-8"))
    assert sync["precision"] == "segment" and sync["words"] == []
    assert "Avsnitt" in sync["precision_note"]
    assert sync["segments"] and sync["blocks"]


def test_export_chapters(env, tmp_path):
    db, store, pid = env
    service = AudioService(store, db, Registry(FakeProvider()))
    service.generate(new_job(), pid)
    files = service.export_chapters(pid, tmp_path / "ut")
    assert [f.name for f in files] == ["01 Innledning.mp3", "02 Metode.mp3"]
    assert all(probe_mp3(f).ok for f in files)


def test_export_chapters_names_the_part_before_the_first_chapter(env, tmp_path):
    db, store, pid = env
    doc = store.load_document(pid)
    doc.blocks.insert(0, Block("b09999", "heading", "Artikkelens tittel", level=1))
    doc.blocks.insert(1, Block("b09998", "paragraph", "Forfatter og sammendrag."))
    store.save_document(pid, doc)
    service = AudioService(store, db, Registry(FakeProvider()))
    service.generate(new_job(), pid)
    names = [f.name for f in service.export_chapters(pid, tmp_path / "ut2")]
    assert names == ["01 Artikkelens tittel.mp3", "02 Innledning.mp3", "03 Metode.mp3"]


@pytest.mark.slow
def test_long_document_is_segmented_and_streamed(app_paths, tmp_path):
    """~60 000 characters (a long article) -> hundreds of segments, one MP3, exact sync."""
    db = Database(app_paths.db)
    store = ProjectStore(app_paths.projects, db)
    src = tmp_path / "lang.txt"
    src.write_text("x", encoding="utf-8")
    pid = store.create(src, "lang.txt", 1)
    blocks = []
    for i in range(400):
        if i % 40 == 0:
            blocks.append(Block(f"b{len(blocks):05d}", "heading", f"Kapittel {i // 40 + 1}", level=1))
        blocks.append(Block(f"b{len(blocks):05d}", "paragraph",
                            f"Avsnitt {i}. Dette er en lengre tekst om lesing, læring og lyd. "
                            "Studentene lytter og følger med på skjermen samtidig, side etter side."))
    store.save_document(pid, Document(title="Lang", source_name="lang.txt", source_type="txt",
                                      size_bytes=1, blocks=blocks, language="nb"))
    store.update_settings(pid, provider="fake", voice_id="v1", language="nb")
    provider = FakeProvider()
    service = AudioService(store, db, Registry(provider))
    _, _, plan, _, _ = service.plan(pid)
    assert plan.total_chars > 50_000 and len(plan.segments) >= 400
    assert all(len(s.text) <= 200 for s in plan.segments)  # FakeProvider prefers 200 chars
    result = service.generate(new_job(), pid)
    sync = json.loads(store.sync_path(pid).read_text(encoding="utf-8"))
    info = probe_mp3(store.audio_path(pid))
    assert info.ok and abs(info.duration - sync["duration"]) < 1.0
    assert len(sync["chapters"]) == 10
    from medleser.speech.providers.piper_align import WORD_RE

    assert len(sync["words"]) == sum(len(WORD_RE.findall(s.text)) for s in plan.segments)  # every word is timed
    times = [w[0] for w in sync["words"]]
    assert times == sorted(times)
    assert result["segments"] == len(plan.segments)
    db.close()


def test_project_reopens_after_restart_without_regenerating(env):
    db, store, pid = env
    service = AudioService(store, db, Registry(FakeProvider()))
    service.generate(new_job(), pid)
    store.update_settings(pid, playback={"position": 12.5})
    db.close()
    # "Restart": new database connection and services over the same folder.
    db2 = Database(store.db.path)
    store2 = ProjectStore(store.root, db2)
    provider = FakeProvider()
    service2 = AudioService(store2, db2, Registry(provider))
    assert store2.load_document(pid).title == "Testdokument"
    settings = store2.load_settings(pid)
    assert settings["playback"]["position"] == 12.5 and settings["audio"]["voice_id"] == "v1"
    state = service2.audio_state(pid)
    assert state["ready"] and not state["stale"]
    assert db2.get_project(pid)["audio_state"] == "ready"
    service2.generate(new_job(), pid)  # nothing missing -> no synthesis calls
    assert provider.calls == []
    db2.close()


def test_id3_tag_structure():
    tag = id3v2_tag(Mp3Tags(title="Æøå tittel", album="Bok", track="1/2", comment="Hei"))
    assert tag[:3] == b"ID3" and tag[3] == 3
    size = (tag[6] << 21) | (tag[7] << 14) | (tag[8] << 7) | tag[9]
    assert size == len(tag) - 10
    assert b"TIT2" in tag and "Æøå tittel".encode("utf-16")[2:] in tag


def test_mp3_writer_streams_and_validates(tmp_path):
    import numpy as np

    path = tmp_path / "x.mp3"
    writer = Mp3Writer(path, 22050, tags=Mp3Tags(title="T"))
    for _ in range(10):
        writer.write((np.random.default_rng(1).standard_normal(22050) * 1000).astype(np.int16))
    writer.close()
    info = probe_mp3(path)
    assert info.ok and abs(info.duration - 10.0) < 0.2
    assert not path.with_name("x.mp3.part").exists()


def test_safe_filename():
    assert safe_filename("..\\..\\evil/CON.pdf") == "_CON.pdf"
    assert safe_filename('a<b>:"c?.docx') == "a_b___c_.docx"
    assert safe_filename("") == "dokument"


def test_project_store_rejects_bad_ids_and_deletes(env):
    db, store, pid = env
    with pytest.raises(UserFacingError):
        store.dir("../../windows")
    assert store.exists(pid)
    store.delete(pid)
    assert not store.exists(pid) and db.get_project(pid) is None
