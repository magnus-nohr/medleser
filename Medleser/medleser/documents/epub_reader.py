"""EPUB 2/3 reader: own minimal parser (zipfile + hardened lxml).

container.xml → OPF package → spine order. Every content document is
parsed with :func:`xmlsafe.parse_xhtml` (no DTDs, no entities, no network).
Paths from the OPF are resolved inside the archive only (``..`` escaping
the root, absolute paths and URLs are ignored) – nothing touches the file
system.

Mapping: h1–h6 → heading (level 1–6); p/div/li/blockquote/figcaption/pre/dt/dd
→ paragraph/list_item/quote/caption; tables → table blocks; script, style,
head, nav, svg, math, forms and media are ignored; page-break markers are
dropped. ``aside`` elements and elements with epub:type
footnote/endnote/rearnote/note (or role doc-footnote/doc-endnote) become
``footnote`` blocks, note references (epub:type="noteref", role
doc-noteref, or a short numeric link inside ``<sup>``) get a
``footnote_ref`` span. epub:type="bibliography" content is marked as
reference list.

DRM: ``META-INF/rights.xml``, ``META-INF/license.lcpl`` (Readium LCP),
``META-INF/sinf.xml`` (FairPlay) or an ``encryption.xml`` that uses anything
other than the two font-obfuscation algorithms → refused. No bypass.
"""

from __future__ import annotations

import posixpath
import re
import threading
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path

from lxml import etree

from ..errors import DocumentImportError
from .cleanup import is_caption, normalize_text, normalize_with_spans
from .common import MSG_EMPTY_DOCUMENT, Progress, ReadResult, check_cancel
from .model import Block, Span
from .postprocess import build_table_data, table_text
from .xmlsafe import UnsafeXMLError, localname, parse_xhtml, parse_xml
from .ziputil import open_zip, read_member

MSG_DRM = "Denne e-boken er kopibeskyttet (DRM) og kan ikke åpnes i Medleser."
MSG_DAMAGED = "E-boken ser ut til å være skadet og kan ikke åpnes."
WARN_SKIPPED = "Noen deler av e-boken kunne ikke leses og er hoppet over."
WARN_TABLE = "Noen tabeller har en uklar struktur. Kontroller dem før de leses opp."

FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
_DRM_FILES = ("meta-inf/rights.xml", "meta-inf/license.lcpl", "meta-inf/sinf.xml")
_CONTENT_TYPES = {"application/xhtml+xml", "text/html", "text/x-oeb1-document", "application/xml"}
OPS_NS = "http://www.idpf.org/2007/ops"
DC_NS = "http://purl.org/dc/elements/1.1/"

_SKIP_TAGS = {
    "script", "style", "head", "title", "meta", "link", "nav", "svg", "math", "template",
    "noscript", "iframe", "object", "embed", "audio", "video", "canvas", "button", "select",
    "textarea", "input", "map", "rt", "rp", "source", "track", "param", "img", "picture",
}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCK_TAGS = {
    "p", "div", "section", "article", "main", "header", "footer", "aside", "blockquote", "li",
    "ul", "ol", "dl", "dt", "dd", "table", "figure", "figcaption", "pre", "hr", "address",
    "details", "summary", "center", "body", "html", "hgroup", "fieldset", "legend", "caption",
    "form", "menu", "dir", "listing", "xmp",
} | set(_HEADINGS)
_NOTE_TYPES = {"footnote", "endnote", "rearnote", "note", "footnotes", "endnotes", "rearnotes"}
_NOTE_ROLES = {"doc-footnote", "doc-endnote", "doc-endnotes", "doc-rearnote", "doc-rearnotes"}
_NOTEREF_TEXT_RE = re.compile(r"^[\[(]?(?:\d{1,3}|[*†‡§¶]{1,3})[\])]?$")


def _attr_types(el: etree._Element) -> set[str]:
    raw = el.get(f"{{{OPS_NS}}}type") or el.get("epub:type") or ""
    return set(raw.lower().split())


def _role(el: etree._Element) -> str:
    return (el.get("role") or "").strip().lower()


def _resolve(base_dir: str, href: str) -> str | None:
    href = (href or "").split("#", 1)[0].strip()
    if not href:
        return None
    href = urllib.parse.unquote(href)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", href) or href.startswith(("/", "\\")):
        return None
    path = posixpath.normpath(posixpath.join(base_dir, href.replace("\\", "/")))
    if path.startswith("../") or path == ".." or path.startswith("/"):
        return None
    return path


def _find_member(exact: set[str], lower: dict[str, str], path: str) -> str | None:
    """Archive member for ``path``; falls back to a case-insensitive match."""
    if path in exact:
        return path
    return lower.get(path.lower())


def _check_drm(zf, lower_names: dict[str, str]) -> None:  # noqa: ANN001
    for drm in _DRM_FILES:
        if drm in lower_names:
            raise DocumentImportError(MSG_DRM, code="epub_drm", details=f"DRM file {drm}")
    enc_name = lower_names.get("meta-inf/encryption.xml")
    if not enc_name:
        return
    try:
        root = parse_xml(read_member(zf, enc_name, max_bytes=5 << 20))
    except (UnsafeXMLError, DocumentImportError) as exc:
        raise DocumentImportError(MSG_DRM, code="epub_drm", details=f"unreadable encryption.xml: {exc}") from exc
    for el in root.iter():
        if localname(el.tag) != "encrypteddata":
            continue
        method = next((c for c in el.iter() if localname(c.tag) == "encryptionmethod"), None)
        alg = method.get("Algorithm") if method is not None else None
        if alg not in FONT_OBFUSCATION:
            raise DocumentImportError(MSG_DRM, code="epub_drm", details=f"encryption algorithm {alg}")


@dataclass(frozen=True)
class _Ctx:
    kind: str = "paragraph"
    note: bool = False
    biblio: bool = False


class _HtmlBlocks:
    """Turns one XHTML content document into blocks."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.buf_len = 0
        self.spans: list[Span] = []
        self.unreliable_tables = 0

    # -- buffer ------------------------------------------------------------
    def add_text(self, s: str | None) -> None:
        if s:
            self.buf.append(s)
            self.buf_len += len(s)

    def add_marker(self, s: str) -> None:
        start = self.buf_len
        self.add_text(s)
        self.spans.append(Span(start, self.buf_len, "footnote_ref"))

    def flush(self, ctx: _Ctx, *, kind: str | None = None, level: int = 0) -> None:
        if not self.buf:
            return
        raw = "".join(self.buf)
        spans = self.spans
        self.buf, self.buf_len, self.spans = [], 0, []
        text, spans = normalize_with_spans(raw, spans)
        if not text:
            return
        k = kind or ctx.kind
        section = None
        if ctx.biblio and k in ("paragraph", "list_item"):
            k, section = "reference", "references"
        elif k == "paragraph" and is_caption(text) and len(text) < 400:
            k = "caption"
        self.blocks.append(Block(id="", kind=k, text=text, level=level if k == "heading" else 0, spans=spans, section=section))

    # -- classification ---------------------------------------------------
    @staticmethod
    def is_note(el: etree._Element) -> bool:
        return (
            localname(el.tag) == "aside"
            or bool(_attr_types(el) & _NOTE_TYPES)
            or _role(el) in _NOTE_ROLES
        )

    @staticmethod
    def skip(el: etree._Element) -> bool:
        tag = localname(el.tag)
        if tag in _SKIP_TAGS:
            return True
        types = _attr_types(el)
        role = _role(el)
        if "pagebreak" in types or role == "doc-pagebreak" or "toc" in types or role == "doc-toc":
            return True
        if el.get("hidden") is not None and not _HtmlBlocks.is_note(el):
            return True
        style = (el.get("style") or "").replace(" ", "").lower()
        return "display:none" in style

    @staticmethod
    def noteref_text(el: etree._Element) -> str | None:
        types = _attr_types(el)
        text = normalize_text("".join(el.itertext()))
        if "noteref" in types or _role(el) == "doc-noteref":
            return text or None
        if localname(el.tag) != "a" or "#" not in (el.get("href") or ""):
            return None
        if not _NOTEREF_TEXT_RE.match(text):
            return None
        parent = el.getparent()
        in_sup = parent is not None and localname(parent.tag) == "sup"
        has_sup = any(localname(c.tag) == "sup" for c in el if isinstance(c.tag, str))
        cls = (el.get("class") or "").lower()
        if in_sup or has_sup or "note" in cls or "fn" in cls:
            return text
        return None

    @staticmethod
    def derive(ctx: _Ctx, el: etree._Element) -> _Ctx:
        tag = localname(el.tag)
        types = _attr_types(el)
        if _HtmlBlocks.is_note(el):
            return replace(ctx, kind="footnote", note=True)
        if "bibliography" in types or _role(el) == "doc-bibliography":
            ctx = replace(ctx, biblio=True)
        if ctx.note:
            return replace(ctx, kind="footnote")
        if tag == "li":
            return replace(ctx, kind="list_item")
        if tag == "blockquote":
            return replace(ctx, kind="quote")
        if tag in ("figcaption", "caption"):
            return replace(ctx, kind="caption")
        if ctx.kind in ("list_item", "quote", "caption"):
            return ctx
        return replace(ctx, kind="paragraph")

    # -- walking ------------------------------------------------------------
    def inline(self, el: etree._Element) -> None:
        if self.skip(el):
            return
        marker = self.noteref_text(el)
        if marker:
            self.add_marker(marker)
            return
        tag = localname(el.tag)
        if tag in ("br", "wbr"):
            self.add_text(" ")
            return
        self.add_text(el.text)
        for child in el:
            if isinstance(child.tag, str):
                if localname(child.tag) in _BLOCK_TAGS:
                    self.add_text(" ")
                    self.inline(child)
                    self.add_text(" ")
                else:
                    self.inline(child)
            self.add_text(child.tail)

    def block(self, el: etree._Element, ctx: _Ctx) -> None:
        if self.skip(el):
            return
        tag = localname(el.tag)
        if tag in _HEADINGS and not ctx.note:
            self.flush(ctx)
            self.add_text(el.text)
            for child in el:
                if isinstance(child.tag, str):
                    self.inline(child)
                self.add_text(child.tail)
            self.flush(ctx, kind="heading", level=_HEADINGS[tag])
            return
        if tag == "table":
            self.flush(ctx)
            self.table(el, ctx)
            return
        if tag == "hr":
            self.flush(ctx)
            return
        inner = self.derive(ctx, el)
        self.flush(ctx)
        self.add_text(el.text)
        for child in el:
            if isinstance(child.tag, str):
                ctag = localname(child.tag)
                if self.skip(child):
                    pass
                elif ctag in _BLOCK_TAGS:
                    self.flush(inner)
                    self.block(child, inner)
                else:
                    self.inline(child)
            self.add_text(child.tail)
        self.flush(inner)

    def cell_text(self, cell: etree._Element) -> tuple[str, bool]:
        saved = (self.buf, self.buf_len, self.spans)
        self.buf, self.buf_len, self.spans = [], 0, []
        nested = any(localname(d.tag) == "table" for d in cell.iterdescendants() if isinstance(d.tag, str))
        self.add_text(cell.text)
        for child in cell:
            if isinstance(child.tag, str):
                self.add_text(" ")
                self.inline(child)
                self.add_text(" ")
            self.add_text(child.tail)
        text = normalize_text("".join(self.buf))
        self.buf, self.buf_len, self.spans = saved
        return text, nested

    def table(self, tbl: etree._Element, ctx: _Ctx) -> None:
        rows: list[list[str]] = []
        header_first = False
        merged = nested = False
        for tr in tbl.iter():
            if not isinstance(tr.tag, str) or localname(tr.tag) != "tr":
                continue
            owner = next((a for a in tr.iterancestors() if localname(a.tag) == "table"), None)
            if owner is not tbl:
                continue
            cells: list[str] = []
            all_th = True
            for cell in tr:
                if not isinstance(cell.tag, str) or localname(cell.tag) not in ("td", "th"):
                    continue
                try:
                    colspan = max(1, int(cell.get("colspan") or 1))
                    rowspan = max(1, int(cell.get("rowspan") or 1))
                except ValueError:
                    colspan = rowspan = 1
                if colspan > 1 or rowspan > 1:
                    merged = True
                text, has_nested = self.cell_text(cell)
                nested = nested or has_nested
                if localname(cell.tag) != "th":
                    all_th = False
                cells.append(text)
                cells.extend([""] * (min(colspan, 50) - 1))
            if not cells:
                continue
            if not rows:
                parent = tr.getparent()
                in_thead = parent is not None and localname(parent.tag) == "thead"
                header_first = in_thead or all_th
            rows.append(cells)
        caption = next((c for c in tbl if isinstance(c.tag, str) and localname(c.tag) == "caption"), None)
        if caption is not None:
            self.add_text(caption.text)
            for child in caption:
                if isinstance(child.tag, str):
                    self.inline(child)
                self.add_text(child.tail)
            self.flush(ctx, kind="caption")
        data = build_table_data(rows, first_row_is_header=header_first, merged=merged, nested=nested)
        if data is None:
            return
        if not data.reliable:
            self.unreliable_tables += 1
        self.blocks.append(Block(id="", kind="table", text=table_text(data), table=data))


def _metadata(opf: etree._Element) -> tuple[str | None, dict[str, str]]:
    meta: dict[str, str] = {}
    title = None
    md = next((c for c in opf.iter() if isinstance(c.tag, str) and localname(c.tag) == "metadata"), None)
    if md is None:
        return None, meta
    multi: dict[str, list[str]] = {}
    for el in md.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(f"{{{DC_NS}}}"):
            continue
        name = localname(el.tag)
        value = normalize_text("".join(el.itertext()))
        if value:
            multi.setdefault(name, []).append(value)
    if multi.get("title"):
        title = multi["title"][0]
    mapping = {
        "creator": "author",
        "contributor": "contributor",
        "publisher": "publisher",
        "date": "date",
        "language": "language",
        "subject": "subject",
        "description": "description",
        "identifier": "identifier",
        "rights": "rights",
    }
    for dc, key in mapping.items():
        if multi.get(dc):
            meta[key] = "; ".join(dict.fromkeys(multi[dc]))[:1000]
    return title, meta


def read_epub(
    path: Path,
    *,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> ReadResult:
    report = progress or Progress(None)
    report(0.0, "Åpner e-boken …")
    zf = open_zip(path)
    with zf:
        exact = set(zf.namelist())
        names = {n.lower(): n for n in exact}
        _check_drm(zf, names)
        container_name = names.get("meta-inf/container.xml")
        if not container_name:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details="missing container.xml")
        try:
            container = parse_xml(read_member(zf, container_name, max_bytes=1 << 20))
        except UnsafeXMLError as exc:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"container.xml: {exc}") from exc
        opf_path = None
        for rf in container.iter():
            if isinstance(rf.tag, str) and localname(rf.tag) == "rootfile":
                candidate = _resolve("", rf.get("full-path") or "")
                if candidate and (rf.get("media-type") in (None, "application/oebps-package+xml")):
                    opf_path = candidate
                    break
        member = _find_member(exact, names, opf_path) if opf_path else None
        if not member:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details="missing OPF package")
        try:
            opf = parse_xml(read_member(zf, member, max_bytes=16 << 20))
        except UnsafeXMLError as exc:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"OPF: {exc}") from exc
        base = posixpath.dirname(member)

        manifest: dict[str, tuple[str, str]] = {}
        spine_ids: list[tuple[str, bool]] = []
        for el in opf.iter():
            if not isinstance(el.tag, str):
                continue
            tag = localname(el.tag)
            if tag == "item" and el.get("id") and el.get("href"):
                resolved = _resolve(base, el.get("href"))
                if resolved:
                    manifest[el.get("id")] = (resolved, (el.get("media-type") or "").lower())
            elif tag == "itemref" and el.get("idref"):
                spine_ids.append((el.get("idref"), (el.get("linear") or "yes").lower() != "no"))
        ordered = [i for i, linear in spine_ids if linear] + [i for i, linear in spine_ids if not linear]
        items: list[str] = []
        for idref in ordered:
            entry = manifest.get(idref)
            if not entry or entry[1] not in _CONTENT_TYPES:
                continue
            found = _find_member(exact, names, entry[0])
            if found and found not in items:
                items.append(found)
        if not items:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details="empty spine")

        title, meta = _metadata(opf)
        builder = _HtmlBlocks()
        skipped = 0
        total = len(items)
        for i, name in enumerate(items):
            check_cancel(cancel)
            report(0.05 + 0.9 * i / total, f"Leser kapittel {i + 1} av {total} …")
            try:
                root = parse_xhtml(read_member(zf, name, max_bytes=64 << 20))
            except (UnsafeXMLError, DocumentImportError):
                skipped += 1
                continue
            body = next(
                (e for e in root.iter() if isinstance(e.tag, str) and localname(e.tag) == "body"), root
            )
            builder.block(body, _Ctx())
            builder.flush(_Ctx())

    if not builder.blocks:
        if skipped:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details="no readable content documents")
        raise DocumentImportError(MSG_EMPTY_DOCUMENT, code="no_text", details="epub without text")
    result = ReadResult(blocks=builder.blocks, title=title, metadata=meta)
    if skipped:
        result.warn("epub_parts_skipped", WARN_SKIPPED)
    if builder.unreliable_tables:
        result.warn("table_uncertain", WARN_TABLE)
    report(1.0, "E-boken er lest.")
    return result
