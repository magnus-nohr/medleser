"""Small, deterministic language identification for document text.

Method
------
Every language has a list of very frequent function words (articles,
pronouns, prepositions, auxiliaries …). Each occurrence of a listed word
scores ``1 / n`` for every language that lists it (``n`` = number of
languages sharing the word), so words such as «og»/«i»/«det» help separate
Scandinavian from other languages while the distinctive words decide
between close relatives: «ikke/ikkje», «jeg/eg», «en/ein», «hva/kva»,
«noe/noko», «fra/frå» (bokmål/nynorsk), «af/av», «efter/etter»,
«nogle/noen», «meget/mye» (dansk/bokmål), «och/og», «inte/ikke»,
«är/er» (svensk). A few characters (æ ø / ä ö / ß / ñ / ç …) add a capped
bonus, which mostly matters for short texts.

Confidence is computed from the evidence that separates the winner from the
runner-up only (words shared by both cancel out):
``margin = (u1 - u2) / (u1 + u2)`` scaled by ``1 - exp(-(u1 - u2) / 3)`` so
that a clear but tiny difference does not look certain.

No dependencies, no randomness – the same text always gives the same answer.
"""

from __future__ import annotations

import math
import re
from collections import Counter

LANGUAGE_NAMES: dict[str, str] = {
    "nb": "Norsk bokmål",
    "nn": "Norsk nynorsk",
    "en": "Engelsk",
    "sv": "Svensk",
    "da": "Dansk",
    "de": "Tysk",
    "fr": "Fransk",
    "es": "Spansk",
    "und": "Ukjent språk",
}

_WORDS: dict[str, str] = {
    "nb": """
        og i det som er på til en et å av for med har de ikke den at jeg seg var
        om men kan vi han hun fra skal vil også eller ble blir være hadde
        etter når så dette disse hva hvordan hvorfor hvor hvem hvilke hvilken hvis
        noe noen mye bare mange flere andre mer mest selv slik både enn mens
        derfor fordi allerede nå her der da ut opp inn mot over under ved hos
        uten gjennom mellom sammen hele eget egne egen deres hennes dere
        meg deg oss sin sitt sine min mitt mine vår vårt våre man må få gjør
        sier vet kunne skulle ville måtte bli blitt hverandre annet ingen alle
        hver hvert kanskje ennå igjen likevel dersom elever elevene skole lærere
        språk ord blant samt altså særlig ofte aldri alltid enda siden
    """,
    "nn": """
        og i det som er på til ein eit å av for med har dei ikkje den at eg seg var
        om men kan vi me han ho frå skal vil òg også eller vart vert blei blir vere vera
        hadde etter når så dette desse kva korleis kvifor kvar kven kva slags viss
        noko nokon nokre mykje berre mange fleire andre meir mest sjølv slik både
        enn medan difor fordi allereie no her der då ut opp inn mot over under ved
        hjå utan gjennom mellom saman heile eige eigne eigen deira hennar dykk
        dykkar meg deg oss sin sitt sine min mitt mine vår vårt våre ein må få
        gjer seier veit kunne skulle ville måtte bli vore kvarandre anna ingen
        alle kvar kvart kanskje enno igjen likevel dersom elevar elevane skule
        lærarar språk ord blant samt altså særleg ofte aldri alltid enno sidan
    """,
    "da": """
        og i det som er på til en et at af for med har de ikke den jeg sig var
        om men kan vi han hun fra skal vil også eller blev bliver være havde
        efter når så dette disse hvad hvordan hvorfor hvor hvem hvilke hvilken
        hvis noget nogle nogen meget bare mange flere andre mere mest selv
        sådan både end mens derfor fordi allerede nu her der da ud op ind mod
        over under ved hos uden gennem mellem sammen hele eget egne egen deres
        hendes jer mig dig os sin sit sine min mit mine vores man må få gør
        siger ved kunne skulle ville måtte blive blevet hinanden andet ingen
        alle hver hvert måske endnu igen alligevel hvilket lidt kun selvom
        elever eleverne skole lærere sprog ord blandt altså især ofte aldrig altid
        siden
    """,
    "sv": """
        och i det som är på till en ett att av för med har de inte den jag sig
        var om men kan vi han hon från ska skall vill också eller blev blir vara
        hade efter när så detta dessa vad hur varför var vem vilka vilken om
        något några någon mycket bara många fler flera andra mer mest själv
        sådan både än medan därför eftersom redan nu här där då ut upp in mot
        över under vid hos utan genom mellan tillsammans hela eget egna egen
        deras hennes mig dig oss sin sitt sina min mitt mina vår vårt våra
        man måste få gör säger vet kunde skulle ville varit varandra annat
        ingen alla varje kanske ännu igen ändå även finns kommer elever eleverna
        skolan lärare språk ord bland särskilt ofta aldrig alltid sedan
    """,
    "en": """
        the a and of to in is that it was for on are with as be by this have
        from or an which not but they their we were has been can would will
        also there what when how all more these other into than its our such
        between about only may should could who whom whose this those them
        he she his her him does did do had being through after before because
        while where why however therefore although both each most some any
        very many much often never always no nor so if then because students
    """,
    "de": """
        und der die das ist nicht ein eine einen einem einer zu den von mit sich in
        des auf für im dem auch es an werden aus hat dass daß sie nach wird bei
        um noch wie über so zum zur war haben nur oder aber vor bis mehr durch
        sein wurde sind wenn können ich wir ihr ihre diese dieser dieses wurden
        kann muss sowie zwischen sehr schon ohne unter weil damit dabei jedoch
        also gegen sondern keine kein einen seine seiner man
    """,
    "fr": """
        le la les de des du et est un une que qui dans pour pas sur au aux avec
        ce cette ces il elle ils elles nous vous sont par plus ne se son sa ses
        leur leurs mais ou où comme été être avoir fait très aussi entre sans
        donc alors tout tous peut même cela ont était lorsque dont ainsi je on
        y en a si
    """,
    "es": """
        el la los las de del que y en un una es por con para no se su sus al lo
        como más pero este esta estos estas son también entre cuando muy sin
        sobre ser hay desde todo fue ha han porque ya le les sido era puede
        otros otra donde si hasta durante mi me yo nos a
    """,
}

# Characters typical for a group of languages (pseudo-tokens, capped).
_CHAR_FEATURES: dict[str, tuple[str, ...]] = {
    "æ": ("nb", "nn", "da"),
    "ø": ("nb", "nn", "da"),
    "å": ("nb", "nn", "da", "sv"),
    "ä": ("sv", "de"),
    "ö": ("sv", "de"),
    "ü": ("de",),
    "ß": ("de",),
    "ñ": ("es",),
    "¿": ("es",),
    "¡": ("es",),
    "á": ("es",),
    "í": ("es",),
    "ó": ("es", "nn"),
    "ú": ("es",),
    "ç": ("fr",),
    "œ": ("fr",),
    "è": ("fr",),
    "ê": ("fr",),
    "à": ("fr",),
    "ù": ("fr",),
    "â": ("fr",),
    "î": ("fr",),
    "ô": ("fr", "nn"),
    "ò": ("nn",),
}
_CHAR_WEIGHT = 0.3
_CHAR_CAP = 10

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_MAX_CHARS = 200_000
_MIN_TOKENS = 4
_MIN_SCORE = 2.0


def _build_index() -> dict[str, tuple[frozenset[str], float]]:
    owners: dict[str, set[str]] = {}
    for lang, words in _WORDS.items():
        for w in words.split():
            owners.setdefault(w, set()).add(lang)
    return {w: (frozenset(langs), 1.0 / len(langs)) for w, langs in owners.items()}


_INDEX = _build_index()
_LANGS = tuple(_WORDS)


def detect_language(text: str) -> tuple[str, float]:
    """Return ``(language_code, confidence)`` for ``text``.

    Codes: nb, nn, en, sv, da, de, fr, es. ``("und", 0.0)`` when there is too
    little text or no recognisable function words. Confidence is 0..1.
    """
    if not text:
        return "und", 0.0
    sample = text[:_MAX_CHARS].lower()
    tokens = _TOKEN_RE.findall(sample)
    if len(tokens) < _MIN_TOKENS:
        return "und", 0.0
    counts = Counter(tokens)

    # contributions: list of (languages, weight * count)
    contributions: list[tuple[frozenset[str], float]] = []
    for word, c in counts.items():
        entry = _INDEX.get(word)
        if entry:
            langs, w = entry
            contributions.append((langs, w * c))
    for ch, langs in _CHAR_FEATURES.items():
        n = sample.count(ch)
        if n:
            contributions.append((frozenset(langs), _CHAR_WEIGHT * min(n, _CHAR_CAP) / len(langs)))

    scores = {lang: 0.0 for lang in _LANGS}
    for langs, value in contributions:
        for lang in langs:
            scores[lang] += value
    ranked = sorted(_LANGS, key=lambda lang: (-scores[lang], _LANGS.index(lang)))
    best, second = ranked[0], ranked[1]
    if scores[best] < _MIN_SCORE or scores[best] < 0.04 * len(tokens):
        return "und", 0.0

    u1 = sum(v for langs, v in contributions if best in langs and second not in langs)
    u2 = sum(v for langs, v in contributions if second in langs and best not in langs)
    if u1 + u2 <= 0:
        return best, 0.0
    diff = u1 - u2
    margin = diff / (u1 + u2)
    strength = 1.0 - math.exp(-diff / 3.0)
    confidence = max(0.0, min(1.0, margin * strength))
    return best, round(confidence, 3)


def language_name(code: str) -> str:
    """Norwegian display name for a language code (falls back to the code)."""
    return LANGUAGE_NAMES.get(code, code)
