"""Build the user guides in veiledninger/*.md as PDF files in dist/.

Usage: .venv\\Scripts\\python.exe scripts\\make_guides.py
The PDFs are included in the installer and attached to the GitHub release.
"""

import sys
from pathlib import Path

import markdown
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "veiledninger"
OUT = ROOT / "dist"
GUIDES = ("Installasjonsveiledning", "Brukerveiledning")

PAGE = pymupdf.paper_rect("a4")
MARGIN = 56  # points (about 2 cm)

CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.45; color: #1b1b1b; }
h1 { font-size: 20pt; color: #1f4e9c; margin: 0 0 10pt 0; }
h2 { font-size: 14pt; color: #1f4e9c; margin: 16pt 0 6pt 0; }
h3 { font-size: 12pt; margin: 12pt 0 4pt 0; }
p, li { margin: 0 0 5pt 0; }
ul, ol { margin: 0 0 6pt 0; }
table { border-collapse: collapse; margin: 4pt 0 8pt 0; width: 100%; }
td, th { border: 0.5pt solid #b8b8b8; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background-color: #eef2f8; }
code { font-family: monospace; font-size: 10pt; }
hr { border: none; border-top: 0.5pt solid #b8b8b8; margin: 12pt 0; }
"""


def to_html(md_text: str) -> str:
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    # Two-column tables without a header row are written with an empty header in Markdown.
    body = body.replace("<thead>\n<tr>\n<th></th>\n<th></th>\n</tr>\n</thead>\n", "")
    return f"<html><body>{body}</body></html>"


def build(name: str) -> Path:
    html = to_html((SOURCE / f"{name}.md").read_text(encoding="utf-8"))
    story = pymupdf.Story(html=html, user_css=CSS)
    target = OUT / f"{name}.pdf"
    writer = pymupdf.DocumentWriter(str(target))
    where = PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN)
    more = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(target)
    title = (SOURCE / f"{name}.md").read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
    doc.set_metadata({"title": title, "author": "Magnus Nohr", "subject": "Medleser",
                      "creator": "Medleser", "producer": "PyMuPDF"})
    for number, page in enumerate(doc, start=1):
        page.insert_text((PAGE.width - MARGIN - 60, PAGE.height - 28), f"Side {number} av {doc.page_count}",
                         fontsize=8, color=(0.4, 0.4, 0.4))
    doc.saveIncr()
    doc.close()
    return target


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for name in GUIDES:
        path = build(name)
        print(f"Ferdig: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
