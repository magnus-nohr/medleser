"""Map Piper phoneme alignments to words of the input text.

Piper (VITS) predicts how many audio frames each phoneme gets. With a voice
model that exposes this output (see ``piper_catalog.patch_for_alignment``),
``PiperVoice.synthesize(..., include_alignments=True)`` returns, per
sentence, the phonemes and the exact number of audio samples each one
occupies. These durations come from the model itself – they are not
estimated – so word timings derived from them are authentic.

espeak-ng separates words with a space phoneme, so normally phoneme words
map 1:1 onto text words. When they don't (numbers, abbreviations, symbols,
words merged by espeak), we align the phoneme stream against a
per-word phonemization with a sequence matcher and give unmatched words the
time between their matched neighbours.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

# Words as the read-along view understands them: letters/digits, allowing
# inner hyphens/apostrophes ("e-post", "IKT-bruk", "don't").
WORD_RE = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_SPECIAL = {"^", "$", "_"}  # BOS, EOS, PAD


@dataclass
class TimedPhoneme:
    phoneme: str
    start: int  # sample offset (absolute within the segment audio)
    end: int


@dataclass
class PhonemeWord:
    phonemes: str
    start: int
    end: int


def text_words(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text)]


def _is_boundary(phoneme: str) -> bool:
    if phoneme.isspace():
        return True
    return all(unicodedata.category(ch).startswith(("P", "S", "Z")) for ch in phoneme)


def phoneme_words(stream: Iterable[TimedPhoneme]) -> list[PhonemeWord]:
    """Group a timed phoneme stream into words (split at spaces/punctuation)."""
    words: list[PhonemeWord] = []
    current: list[TimedPhoneme] = []

    def close() -> None:
        if current:
            words.append(
                PhonemeWord("".join(p.phoneme for p in current), current[0].start, current[-1].end)
            )
            current.clear()

    for item in stream:
        if item.phoneme in _SPECIAL:
            close()
            continue
        if _is_boundary(item.phoneme):
            close()
            continue
        current.append(item)
    close()
    return words


def _letters(phonemes: str) -> str:
    """Keep only base phoneme letters (drop stress/length marks and diacritics)."""
    out = []
    for ch in unicodedata.normalize("NFD", phonemes):
        cat = unicodedata.category(ch)
        if cat in ("Ll", "Lu", "Lo", "Lt"):
            out.append(ch)
    return "".join(out)


def align_words(
    text: str,
    pwords: Sequence[PhonemeWord],
    phonemize_word: Optional[Callable[[str], str]] = None,
) -> Optional[list[tuple[int, int, int, int]]]:
    """Return ``[(char_start, char_end, sample_start, sample_end), …]`` or None.

    ``phonemize_word`` (word text -> phoneme string) is only needed for the
    fallback path when the counts differ.
    """
    words = text_words(text)
    if not words or not pwords:
        return None

    if len(words) == len(pwords):
        return [(cs, ce, pw.start, pw.end) for (cs, ce), pw in zip(words, pwords)]

    if phonemize_word is None:
        return None

    # Fallback: character-level alignment of expected vs. produced phonemes.
    expected_chars: list[str] = []
    expected_word: list[int] = []
    for wi, (cs, ce) in enumerate(words):
        letters = _letters(phonemize_word(text[cs:ce]))
        expected_chars.extend(letters)
        expected_word.extend([wi] * len(letters))

    produced_chars: list[str] = []
    produced_pos: list[tuple[int, int]] = []
    for pw in pwords:
        letters = _letters(pw.phonemes)
        if not letters:
            continue
        # Spread the group's samples over its letters only for *locating*
        # matches; the word boundaries we output still come from group edges.
        n = len(letters)
        span = pw.end - pw.start
        for k, ch in enumerate(letters):
            produced_chars.append(ch)
            produced_pos.append((pw.start + span * k // n, pw.start + span * (k + 1) // n))

    if not expected_chars or not produced_chars:
        return None

    matcher = difflib.SequenceMatcher(None, expected_chars, produced_chars, autojunk=False)
    starts: list[Optional[int]] = [None] * len(words)
    ends: list[Optional[int]] = [None] * len(words)
    matched = 0
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            wi = expected_word[block.a + k]
            s, e = produced_pos[block.b + k]
            starts[wi] = s if starts[wi] is None else min(starts[wi], s)
            ends[wi] = e if ends[wi] is None else max(ends[wi], e)
            matched += 1

    if matched < 0.5 * len(expected_chars):
        return None  # too different – don't pretend we know word timing

    # Snap matched words to phoneme-group edges (more faithful than the
    # per-letter split used for matching).
    def snap(sample: int, use_start: bool) -> int:
        for pw in pwords:
            if pw.start <= sample < pw.end or (not use_start and pw.start < sample <= pw.end):
                return pw.start if use_start else pw.end
        return sample

    for wi in range(len(words)):
        if starts[wi] is not None:
            starts[wi] = snap(starts[wi], True)
            ends[wi] = snap(ends[wi], False)

    # Unmatched words get the gap between matched neighbours (bounded by
    # authentic timings on both sides, never extrapolated).
    result: list[tuple[int, int, int, int]] = []
    for wi, (cs, ce) in enumerate(words):
        s, e = starts[wi], ends[wi]
        if s is None:
            prev_end = next((ends[j] for j in range(wi - 1, -1, -1) if ends[j] is not None), None)
            next_start = next((starts[j] for j in range(wi + 1, len(words)) if starts[j] is not None), None)
            if prev_end is None:
                prev_end = pwords[0].start
            if next_start is None:
                next_start = pwords[-1].end
            if next_start <= prev_end:
                # No room: share the neighbour's boundary (zero-length word).
                s = e = prev_end
            else:
                s, e = prev_end, next_start
        result.append((cs, ce, int(s), int(e)))

    # Enforce monotonic, non-overlapping order.
    last_end = 0
    fixed = []
    for cs, ce, s, e in result:
        s = max(s, last_end)
        e = max(e, s)
        fixed.append((cs, ce, s, e))
        last_end = e if e > s else last_end
    return fixed
