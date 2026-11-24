"""Check the pinned Piper files against Hugging Face (free, needs internet) – OFF by default.

    .venv/Scripts/python.exe -m pytest -m integration tests/integration/test_piper_pins_live.py -q
"""

import httpx
import pytest

from medleser.speech.providers import piper_catalog

pytestmark = pytest.mark.integration


def test_every_pinned_file_exists_unchanged_at_the_pinned_revision():
    response = httpx.get(piper_catalog.BASE_URL + "voices.json", follow_redirects=True, timeout=60)
    response.raise_for_status()
    published = response.json()
    for voice in piper_catalog.CATALOG:
        files = published[voice.key]["files"]
        for suffix, size, md5 in ((".onnx", voice.onnx_size, voice.onnx_md5),
                                  (".onnx.json", voice.json_size, voice.json_md5)):
            entry = files[f"{voice.path}{voice.key}{suffix}"]
            assert (entry["size_bytes"], entry["md5_digest"]) == (size, md5), f"{voice.key}{suffix}"
            head = httpx.head(f"{piper_catalog.BASE_URL}{voice.path}{voice.key}{suffix}", follow_redirects=True, timeout=60)
            assert head.status_code == 200, f"{voice.key}{suffix}"
