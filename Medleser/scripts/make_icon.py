"""Render web/img/icon.svg to assets/medleser.ico (PNG-compressed ICO, 16–256 px)."""

import struct
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(svg: Path, size: int) -> bytes:
    doc = pymupdf.open(str(svg))
    page = doc[0]
    zoom = size / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    if pix.width != size or pix.height != size:
        pix = pymupdf.Pixmap(pix, size, size, None)
    return pix.tobytes("png")


def main() -> int:
    svg = ROOT / "web" / "img" / "icon.svg"
    out = ROOT / "assets" / "medleser.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    images = [(s, render_png(svg, s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    offset = 6 + 16 * len(images)
    data = b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    out.write_bytes(header + entries + data)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
