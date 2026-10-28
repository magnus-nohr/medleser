import pytest

from medleser.documents.language import LANGUAGE_NAMES, detect_language

SAMPLES = {
    "nb": (
        "Studenter med dysleksi bruker ofte lengre tid på å lese faglitteratur. Det er derfor viktig at "
        "de får tilgang til hjelpemidler som kan lese teksten høyt. I denne undersøkelsen har vi sett på "
        "hvordan studentene opplever slike verktøy, og hva som gjør at noen velger å bruke dem mer enn andre."
    ),
    "nn": (
        "Studentar med dysleksi brukar ofte lengre tid på å lese faglitteratur. Det er difor viktig at dei "
        "får tilgang til hjelpemiddel som kan lese teksten høgt. I denne undersøkinga har vi sett på korleis "
        "studentane opplever slike verktøy, og kva som gjer at nokon vel å bruke dei meir enn andre."
    ),
    "en": (
        "Students with dyslexia often spend more time reading academic texts. It is therefore important "
        "that they have access to tools that can read the text aloud. In this study we examined how the "
        "students experience such tools, and what makes some of them use these more than others."
    ),
    "sv": (
        "Studenter med dyslexi använder ofta längre tid för att läsa facklitteratur. Det är därför viktigt "
        "att de får tillgång till hjälpmedel som kan läsa texten högt. I den här undersökningen har vi "
        "tittat på hur studenterna upplever sådana verktyg och vad som gör att några väljer att använda dem."
    ),
    "da": (
        "Studerende med ordblindhed bruger ofte længere tid på at læse faglitteratur. Det er derfor vigtigt, "
        "at de får adgang til hjælpemidler, som kan læse teksten højt. I denne undersøgelse har vi set på, "
        "hvordan de studerende oplever sådanne værktøjer, og hvad der gør, at nogle vælger at bruge dem mere."
    ),
    "de": (
        "Studierende mit Legasthenie brauchen oft mehr Zeit, um Fachliteratur zu lesen. Es ist daher wichtig, "
        "dass sie Zugang zu Hilfsmitteln haben, die den Text vorlesen können. In dieser Untersuchung haben wir "
        "betrachtet, wie die Studierenden solche Werkzeuge erleben."
    ),
    "fr": (
        "Les étudiants dyslexiques passent souvent plus de temps à lire des textes académiques. Il est donc "
        "important qu'ils aient accès à des outils qui peuvent lire le texte à voix haute. Dans cette étude, "
        "nous avons examiné comment les étudiants perçoivent ces outils."
    ),
    "es": (
        "Los estudiantes con dislexia suelen dedicar más tiempo a leer textos académicos. Por eso es importante "
        "que tengan acceso a herramientas que puedan leer el texto en voz alta. En este estudio examinamos cómo "
        "los estudiantes perciben estas herramientas."
    ),
}


@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_detects_language(code):
    lang, confidence = detect_language(SAMPLES[code])
    assert lang == code
    assert 0.5 <= confidence <= 1.0


@pytest.mark.parametrize("text", ["", "Hei", "Dette er en test.", "12 34 56 78 90", "   \n  "])
def test_too_little_text_is_undetermined(text):
    assert detect_language(text) == ("und", 0.0)


def test_deterministic_and_confidence_reflects_margin():
    long_nb = SAMPLES["nb"] * 3
    assert detect_language(long_nb) == detect_language(long_nb)
    # a mix of bokmål and nynorsk is less certain than clean bokmål
    mixed = SAMPLES["nb"] + " " + SAMPLES["nn"]
    assert detect_language(mixed)[1] < detect_language(long_nb)[1]


def test_unknown_language_is_und():
    finnish = "Opiskelijat lukevat kirjoja kirjastossa joka päivä ja kirjoittavat esseitä yliopistolle."
    assert detect_language(finnish)[0] == "und"


def test_language_names_are_norwegian():
    assert LANGUAGE_NAMES["nb"] == "Norsk bokmål"
    assert LANGUAGE_NAMES["nn"] == "Norsk nynorsk"
    assert LANGUAGE_NAMES["en"] == "Engelsk"
    for code in ("sv", "da", "de", "fr", "es", "und"):
        assert LANGUAGE_NAMES[code]
