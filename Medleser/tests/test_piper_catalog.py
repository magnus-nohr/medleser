"""Order, recommendations and retired voices in the curated Piper catalog."""

import pytest

from medleser.errors import UserFacingError
from medleser.speech.providers import piper_catalog
from medleser.speech.providers.piper_provider import PiperProvider

NVCC = "no_NO-nvcc-medium"


def test_talesyntese_is_the_only_norwegian_voice_and_recommended():
    norwegian = [v for v in piper_catalog.CATALOG if v.language == "nb"]
    assert [v.key for v in norwegian] == ["no_NO-talesyntese-medium"]
    assert norwegian[0].recommended


def test_each_language_has_exactly_one_recommended_voice():
    for language in {v.language for v in piper_catalog.CATALOG if v.recommended}:
        recommended = [v for v in piper_catalog.CATALOG if v.language == language and v.recommended]
        assert len(recommended) == 1, language


def test_retired_voices_cannot_be_downloaded_or_used(tmp_path):
    assert NVCC in piper_catalog.RETIRED_KEYS
    assert NVCC not in piper_catalog.CATALOG_BY_KEY
    with pytest.raises(UserFacingError):
        piper_catalog.voice_dir(tmp_path, NVCC)


def test_installed_retired_voice_is_hidden_and_removed(tmp_path):
    old = tmp_path / NVCC
    old.mkdir()
    (old / f"{NVCC}.onnx").write_bytes(b"x")
    (old / f"{NVCC}.onnx.json").write_text("{}", encoding="utf-8")
    (old / piper_catalog.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    keep = tmp_path / "no_NO-talesyntese-medium"
    keep.mkdir()

    assert not any(v.id.startswith(NVCC) for v in PiperProvider(tmp_path).list_voices())
    assert piper_catalog.remove_retired_voices(tmp_path) == [NVCC]
    assert not old.exists()
    assert keep.exists()
    assert piper_catalog.remove_retired_voices(tmp_path) == []


def test_retired_voice_cleanup_ignores_missing_root(tmp_path):
    assert piper_catalog.remove_retired_voices(tmp_path / "finnes-ikke") == []
