"""MP3 encoding (LAME via ``lameenc``), ID3 tags and verification.

Audio is streamed segment by segment into one encoder, so a long book is
never held in memory as a whole.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


# LAME's encoder + decoder delay. lameenc writes no gapless (LAME/Xing) header, so
# decoders – including WebView2/Chromium – play these samples before the audio.
# Measured in WebView2: click at 1.000 s decoded at 1.0501 s (22.05 kHz), 1.0690 s
# (16 kHz) and 1.0460 s (24 kHz) = exactly 1105 samples. The read-along view shifts
# its clock by this amount (sync.json "audio_offset").
LAME_DELAY_SAMPLES = 1105


def lame_delay_seconds(sample_rate: int) -> float:
    return LAME_DELAY_SAMPLES / float(sample_rate) if sample_rate else 0.0


def default_bitrate(sample_rate: int) -> int:
    # Speech, mono: 64 kbit/s is transparent at 16–24 kHz; a bit more for 44.1 kHz.
    return 64 if sample_rate <= 24000 else 96


@dataclass
class Mp3Tags:
    title: str = ""
    artist: str = "Medleser"
    album: str = ""
    track: Optional[str] = None  # "3/12"
    genre: str = "Speech"
    comment: str = ""
    language: str = "nor"  # ISO 639-2 for the COMM frame


def _text_frame(frame_id: str, text: str) -> bytes:
    payload = b"\x01" + text.encode("utf-16")  # UTF-16 with BOM (ID3v2.3 encoding 1)
    return frame_id.encode("ascii") + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


def _comment_frame(text: str, language: str) -> bytes:
    lang = (language or "nor")[:3].ljust(3, " ").encode("ascii", "replace")
    payload = b"\x01" + lang + "".encode("utf-16") + b"\x00\x00" + text.encode("utf-16")
    return b"COMM" + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v2_tag(tags: Mp3Tags) -> bytes:
    frames = b""
    if tags.title:
        frames += _text_frame("TIT2", tags.title[:250])
    if tags.artist:
        frames += _text_frame("TPE1", tags.artist[:250])
    if tags.album:
        frames += _text_frame("TALB", tags.album[:250])
    if tags.track:
        frames += _text_frame("TRCK", tags.track)
    if tags.genre:
        frames += _text_frame("TCON", tags.genre)
    if tags.comment:
        frames += _comment_frame(tags.comment[:500], tags.language)
    return b"ID3" + bytes([3, 0, 0]) + _syncsafe(len(frames)) + frames


class Mp3Writer:
    """Streaming MP3 writer. Writes to ``path + '.part'`` and renames on close."""

    def __init__(self, path: Path, sample_rate: int, *, bitrate: Optional[int] = None,
                 tags: Optional[Mp3Tags] = None) -> None:
        import lameenc

        self.path = Path(path)
        self.sample_rate = int(sample_rate)
        self._tmp = self.path.with_name(self.path.name + ".part")
        self._fh = open(self._tmp, "wb")
        if tags is not None:
            self._fh.write(id3v2_tag(tags))
        self._enc = lameenc.Encoder()
        self._enc.set_bit_rate(bitrate or default_bitrate(self.sample_rate))
        self._enc.set_in_sample_rate(self.sample_rate)
        self._enc.set_channels(1)
        self._enc.set_quality(2)
        self.samples_written = 0
        self._closed = False

    def write(self, pcm: np.ndarray) -> None:
        if len(pcm) == 0:
            return
        data = np.asarray(pcm, dtype="<i2").tobytes()
        self._fh.write(self._enc.encode(data))
        self.samples_written += len(pcm)

    def close(self) -> Path:
        if self._closed:
            return self.path
        self._fh.write(self._enc.flush())
        self._fh.close()
        os.replace(self._tmp, self.path)
        self._closed = True
        return self.path

    def abort(self) -> None:
        if self._closed:
            return
        try:
            self._fh.close()
        finally:
            self._tmp.unlink(missing_ok=True)
            self._closed = True

    @property
    def duration(self) -> float:
        return self.samples_written / float(self.sample_rate)


@dataclass
class Mp3Info:
    ok: bool
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    error: Optional[str] = None


def probe_mp3(path: Path) -> Mp3Info:
    """Decode-level check that the file is a playable MP3."""
    try:
        import miniaudio

        info = miniaudio.mp3_get_file_info(str(path))
        if info.num_frames <= 0 or info.sample_rate <= 0:
            return Mp3Info(False, error="no audio frames")
        return Mp3Info(True, info.duration, info.sample_rate, info.nchannels)
    except Exception as exc:  # noqa: BLE001
        return Mp3Info(False, error=repr(exc))


def decode_mp3_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Decode MP3 bytes to mono int16 PCM (used for cloud audio in MP3 format)."""
    import miniaudio

    decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1)
    pcm = np.frombuffer(decoded.samples.tobytes(), dtype="<i2").astype(np.int16)
    return pcm, decoded.sample_rate
