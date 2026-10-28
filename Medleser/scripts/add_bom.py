"""Add a UTF-8 byte order mark to text files (Inno Setup and Windows PowerShell 5.1 need it)."""

import sys
from pathlib import Path

for name in sys.argv[1:]:
    path = Path(name)
    text = path.read_text(encoding="utf-8-sig")  # read first, then write
    path.write_text(text, encoding="utf-8-sig")
    print(f"{path}: {len(text)} tegn, BOM={path.read_bytes()[:3] == b'\xef\xbb\xbf'}")
