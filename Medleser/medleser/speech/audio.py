"""Small PCM helpers (mono int16)."""

from __future__ import annotations

import json
import os
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np


def silence(seconds: float, sample_rate: int) -> np.ndarray:
    return np.zeros(max(0, int(round(seconds * sample_rate))), dtype=np.int16)


def apply_fades(pcm: np.ndarray, sample_rate: int, ms: float = 6.0) -> np.ndarray:
    """Short linear fade in/out so joins between segments never click."""
    n = min(len(pcm) // 2, int(sample_rate * ms / 1000.0))
    if n <= 1:
        return pcm
    out = pcm.astype(np.float32)
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out[:n] *= ramp
    out[-n:] *= ramp[::-1]
    return out.astype(np.int16)


def resample(pcm: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampler (adequate for speech, rarely needed)."""
    if src_rate == dst_rate or len(pcm) == 0:
        return pcm
    duration = len(pcm) / float(src_rate)
    n_out = int(round(duration * dst_rate))
    x_old = np.linspace(0.0, duration, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)


def write_wav(path: Path, pcm: np.ndarray, sample_rate: int) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with wave.open(str(tmp), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
    os.replace(tmp, path)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
        channels = wav.getnchannels()
    pcm = np.frombuffer(frames, dtype="<i2").astype(np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return pcm, rate


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
