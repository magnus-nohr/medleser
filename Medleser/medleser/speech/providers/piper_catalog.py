"""Curated catalog of Piper voices + safe download/installation.

Only voices whose training data licence allows free use are listed. Files
are downloaded from the official Piper voice repository on Hugging Face and
verified against the size and MD5 digest pinned below (taken from the
repository's voices.json), so a tampered or truncated download is rejected.

After download, the model is patched so it also outputs per-phoneme
durations (``piper.patch_voice_with_alignment``); this enables authentic
word-level highlighting. The original file's digest is recorded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ...errors import CancelledError, UserFacingError

_LOG = logging.getLogger(__name__)

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
MANIFEST_NAME = "medleser-voice.json"


@dataclass(frozen=True)
class CatalogVoice:
    key: str
    path: str  # folder in the repository
    language: str  # "nb", "en", "sv", "da"
    name: str  # display name
    description: str  # Norwegian
    license: str  # Norwegian description of the licence
    onnx_size: int
    onnx_md5: str
    json_size: int
    json_md5: str
    recommended: bool = False
    gender: Optional[str] = None
    speaker_labels: Optional[dict[str, str]] = None  # code -> label (multi-speaker)

    @property
    def size_mb(self) -> int:
        return round(self.onnx_size / 1_000_000)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "language": self.language,
            "name": self.name,
            "description": self.description,
            "license": self.license,
            "size_mb": self.size_mb,
            "recommended": self.recommended,
            "gender": self.gender,
            "speakers": len(self.speaker_labels or {}) or 1,
        }


_LESSAC_NOTE = (
    "Finjustert fra den engelske «lessac»-stemmen, som har egne lisensvilkår. Bruk til privat lesing."
)

CATALOG: tuple[CatalogVoice, ...] = (
    # Talesyntese is the only Norwegian voice: in listening tests (2026-10-28) it gave clear
    # bokmål, while the NVCC dialect voices gave unusable results and were removed.
    CatalogVoice(
        key="no_NO-talesyntese-medium",
        path="no/no_NO/talesyntese/medium/",
        language="nb",
        name="Norsk (Talesyntese)",
        description="Én norsk mannsstemme basert på Språkbankens talesyntesemateriale.",
        license="Opptakene er CC0 (Språkbanken). " + _LESSAC_NOTE,
        onnx_size=63201294,
        onnx_md5="9fc876e7edc6593086b4f2f34889f44b",
        json_size=4880,
        json_md5="94b938e1bd319e6a084dc8380fb53619",
        recommended=True,
        gender="male",
    ),
    CatalogVoice(
        key="en_GB-cori-medium",
        path="en/en_GB/cori/medium/",
        language="en",
        name="Engelsk, britisk (Cori)",
        description="Britisk-engelsk kvinnestemme trent på lydbøker fra LibriVox.",
        license="Allemannseie (public domain, LibriVox). Trent fra bunnen av.",
        onnx_size=63531379,
        onnx_md5="f143307611eccea9d976235d0895f57c",
        json_size=4966,
        json_md5="12b1dc45d8919f3475cf296d5f16a4c6",
        recommended=True,
        gender="female",
    ),
    CatalogVoice(
        key="en_GB-cori-high",
        path="en/en_GB/cori/high/",
        language="en",
        name="Engelsk, britisk (Cori, høy oppløsning)",
        description="Samme stemme som Cori, i Pipers «high»-variant. Større fil og litt tregere.",
        license="Allemannseie (public domain, LibriVox). Trent fra bunnen av.",
        onnx_size=114219352,
        onnx_md5="3474a80133d9a03e6870d2ac42c18806",
        json_size=4963,
        json_md5="0f7d42e77a99193006aa34a34442f5e0",
        gender="female",
    ),
    CatalogVoice(
        key="en_US-kristin-medium",
        path="en/en_US/kristin/medium/",
        language="en",
        name="Engelsk, amerikansk (Kristin)",
        description="Amerikansk-engelsk kvinnestemme trent på lydbøker fra LibriVox.",
        license="Allemannseie (public domain, LibriVox). Trent fra bunnen av.",
        onnx_size=63531379,
        onnx_md5="5fed42d2296baca042e2bf74785db725",
        json_size=4968,
        json_md5="70bc97d350c796c64ea5e4d08241afac",
        gender="female",
    ),
    CatalogVoice(
        key="en_US-norman-medium",
        path="en/en_US/norman/medium/",
        language="en",
        name="Engelsk, amerikansk (Norman)",
        description="Amerikansk-engelsk mannsstemme trent på lydbøker fra LibriVox.",
        license="Allemannseie (public domain, LibriVox). Trent fra bunnen av.",
        onnx_size=63531379,
        onnx_md5="829cea515dc724d694b83b71e8083f9f",
        json_size=4968,
        json_md5="975830d6f230f6eccf657d265de99eba",
        gender="male",
    ),
    CatalogVoice(
        key="sv_SE-nst-medium",
        path="sv/sv_SE/nst/medium/",
        language="sv",
        name="Svensk (NST)",
        description="Svensk stemme trent av KBLab (Kungliga biblioteket) på NST-materialet.",
        license="CC0 (Språkbanken). Trent fra bunnen av.",
        onnx_size=63104526,
        onnx_md5="20266cf58e93ca2140444b77398aea04",
        json_size=4157,
        json_md5="3a95d9b88bb3214bf4d0d7fcf8a1aea9",
    ),
    CatalogVoice(
        key="da_DK-talesyntese-medium",
        path="da/da_DK/talesyntese/medium/",
        language="da",
        name="Dansk (Talesyntese)",
        description="Dansk stemme basert på Språkbankens talesyntesemateriale.",
        license="Opptakene er CC0 (Språkbanken). " + _LESSAC_NOTE,
        onnx_size=63201294,
        onnx_md5="9c05494a3e0c1136337581e01222395d",
        json_size=4878,
        json_md5="8a56843f52992e490f062e68c14fa193",
    ),
)

CATALOG_BY_KEY = {v.key: v for v in CATALOG}

# Voices that earlier versions could download but that are no longer offered.
# Their folders are deleted at startup so they do not take up space.
RETIRED_KEYS: tuple[str, ...] = ("no_NO-nvcc-medium",)

ProgressFn = Callable[[float, str], None]


def voice_dir(root: Path, key: str) -> Path:
    # Only catalog keys are valid; this also rules out path tricks such as "..\\x".
    if key not in CATALOG_BY_KEY:
        raise UserFacingError("Ukjent stemme.", code="unknown_voice", status=404)
    return root / key


def model_path(root: Path, key: str) -> Path:
    return voice_dir(root, key) / f"{key}.onnx"


def read_manifest(root: Path, key: str) -> Optional[dict]:
    if key not in CATALOG_BY_KEY:
        return None
    path = voice_dir(root, key) / MANIFEST_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_installed(root: Path, key: str) -> bool:
    if key not in CATALOG_BY_KEY:
        return False
    manifest = read_manifest(root, key)
    return bool(manifest) and model_path(root, key).exists() and (
        voice_dir(root, key) / f"{key}.onnx.json"
    ).exists()


def installed_keys(root: Path) -> list[str]:
    return [v.key for v in CATALOG if is_installed(root, v.key)]


def remove_retired_voices(root: Path) -> list[str]:
    """Delete downloaded voices that are no longer in the catalog. Returns the removed keys."""
    removed: list[str] = []
    resolved_root = root.resolve()
    for key in RETIRED_KEYS:
        target = (root / key).resolve()
        if target.parent != resolved_root or not target.is_dir():
            continue
        shutil.rmtree(target, ignore_errors=True)
        if not target.exists():
            removed.append(key)
    return removed


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - integrity check against the publisher's MD5 list
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def patch_for_alignment(src: Path, dst: Path) -> bool:
    """Write a copy of the model that also outputs phoneme durations.

    Returns False (and copies the model unchanged) if patching is not
    possible; the voice then falls back to sentence-level highlighting.
    """
    try:
        import onnx

        from piper.patch_voice_with_alignment import add_alignment_output
    except ImportError as exc:
        _LOG.warning("onnx not available, cannot patch voice for alignment: %s", exc)
        if src != dst:
            shutil.copyfile(src, dst)
        return False
    model = onnx.load(str(src))
    try:
        add_alignment_output(model)
    except ValueError as exc:
        # Already patched or unknown structure.
        _LOG.info("Alignment patch not applied: %s", exc)
        if src != dst:
            shutil.copyfile(src, dst)
        return "already" in str(exc)
    onnx.save(model, str(dst))
    return True


def _download(
    client,
    url: str,
    dest: Path,
    expected_size: int,
    expected_md5: str,
    progress: Optional[Callable[[int], None]],
    cancel: Optional[threading.Event],
) -> None:
    digest = hashlib.md5()  # noqa: S324
    received = 0
    with client.stream("GET", url, follow_redirects=True) as response:
        if response.status_code != 200:
            raise UserFacingError(
                "Stemmen kunne ikke lastes ned akkurat nå. Prøv igjen senere.",
                code="download_failed",
                details=f"HTTP {response.status_code} for {url}",
            )
        with open(dest, "wb") as fh:
            for chunk in response.iter_bytes(1 << 16):
                if cancel is not None and cancel.is_set():
                    raise CancelledError("Nedlastingen ble avbrutt.")
                fh.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if received > expected_size + 1024:
                    raise UserFacingError(
                        "Nedlastingen var større enn forventet og ble stoppet av sikkerhetshensyn.",
                        code="download_invalid",
                    )
                if progress:
                    progress(len(chunk))
    if received != expected_size or digest.hexdigest() != expected_md5:
        raise UserFacingError(
            "Den nedlastede stemmefilen var ufullstendig eller endret, og ble forkastet. Prøv igjen.",
            code="download_invalid",
            details=f"size={received}/{expected_size} md5={digest.hexdigest()}/{expected_md5}",
        )


def install_voice(
    root: Path,
    key: str,
    *,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[threading.Event] = None,
    client=None,
) -> None:
    """Download, verify and prepare a catalog voice (idempotent)."""
    voice = CATALOG_BY_KEY.get(key)
    if voice is None:
        raise UserFacingError("Ukjent stemme.", code="unknown_voice", status=404)
    if is_installed(root, key):
        if progress:
            progress(1.0, "Stemmen er allerede installert.")
        return

    root.mkdir(parents=True, exist_ok=True)
    final_dir = voice_dir(root, key)
    tmp_dir = root / f".tmp-{key}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)

    own_client = client is None
    if own_client:
        import httpx

        client = httpx.Client(timeout=httpx.Timeout(30.0, read=60.0), headers={"User-Agent": "Medleser/1.0"})
    try:
        total = voice.onnx_size + voice.json_size
        done = 0

        def report(n: int) -> None:
            nonlocal done
            done += n
            if progress:
                progress(min(0.9, 0.9 * done / total), "Laster ned stemmen …")

        # Reuse an earlier verified download if one is lying in the target folder.
        existing = model_path(root, key)
        onnx_tmp = tmp_dir / f"{key}.onnx.download"
        if existing.exists() and existing.stat().st_size == voice.onnx_size and _md5(existing) == voice.onnx_md5:
            shutil.copyfile(existing, onnx_tmp)
            report(voice.onnx_size)
        else:
            _download(client, BASE_URL + voice.path + f"{key}.onnx", onnx_tmp,
                      voice.onnx_size, voice.onnx_md5, report, cancel)
        _download(client, BASE_URL + voice.path + f"{key}.onnx.json", tmp_dir / f"{key}.onnx.json",
                  voice.json_size, voice.json_md5, report, cancel)
        try:
            response = client.get(BASE_URL + voice.path + "MODEL_CARD", follow_redirects=True)
            if response.status_code == 200 and len(response.content) < 100_000:
                (tmp_dir / "MODEL_CARD").write_bytes(response.content)
        except Exception:  # noqa: BLE001 - the model card is informational only
            pass

        if cancel is not None and cancel.is_set():
            raise CancelledError("Nedlastingen ble avbrutt.")
        if progress:
            progress(0.92, "Klargjør stemmen for ord-for-ord-markering …")
        aligned = patch_for_alignment(onnx_tmp, tmp_dir / f"{key}.onnx")
        if (tmp_dir / f"{key}.onnx").exists():
            onnx_tmp.unlink(missing_ok=True)
        else:
            onnx_tmp.rename(tmp_dir / f"{key}.onnx")
        (tmp_dir / MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "key": key,
                    "source_md5": voice.onnx_md5,
                    "aligned": aligned,
                    "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        shutil.rmtree(final_dir, ignore_errors=True)
        tmp_dir.rename(final_dir)
        if progress:
            progress(1.0, "Stemmen er klar.")
    finally:
        if own_client:
            client.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def remove_voice(root: Path, key: str) -> None:
    if key not in CATALOG_BY_KEY:
        raise UserFacingError("Ukjent stemme.", code="unknown_voice", status=404)
    target = voice_dir(root, key).resolve()
    if target.parent != root.resolve():
        raise UserFacingError("Ugyldig stemmemappe.", code="invalid_path")
    shutil.rmtree(target, ignore_errors=True)
