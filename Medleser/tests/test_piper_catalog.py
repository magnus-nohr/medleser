"""Order and recommendations in the curated Piper catalog."""

from medleser.speech.providers import piper_catalog


def test_talesyntese_is_the_recommended_norwegian_voice_and_listed_first():
    norwegian = [v for v in piper_catalog.CATALOG if v.language == "nb"]
    assert norwegian[0].key == "no_NO-talesyntese-medium"
    assert [v.key for v in norwegian if v.recommended] == ["no_NO-talesyntese-medium"]


def test_each_language_has_exactly_one_recommended_voice():
    for language in {v.language for v in piper_catalog.CATALOG if v.recommended}:
        recommended = [v for v in piper_catalog.CATALOG if v.language == language and v.recommended]
        assert len(recommended) == 1, language
