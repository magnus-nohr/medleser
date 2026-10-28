"""Hardened XML/XHTML parsing for document containers (DOCX parts, EPUB files).

* Entity declarations (``<!ENTITY``) are always refused – no billion-laughs,
  no external entities.
* DTDs are never loaded, nothing is fetched from the network
  (``load_dtd=False, no_network=True, resolve_entities=False``).
* ``parse_xml`` refuses any DOCTYPE (like defusedxml's ``forbid_dtd``).
* ``parse_xhtml`` removes a plain ``<!DOCTYPE html …>`` declaration (common in
  EPUB files), replaces HTML named entities (``&nbsp;``) by numeric character
  references and parses strictly; if that fails, it falls back to lxml's
  forgiving HTML parser (which also never loads DTDs or external resources).
"""

from __future__ import annotations

import html.entities
import re

from lxml import etree
from lxml import html as lxml_html


class UnsafeXMLError(ValueError):
    """The XML contains constructs we refuse to parse (entities, DOCTYPE …)."""


def _strict_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )


_ENTITY_DECL_RE = re.compile(r"<!ENTITY", re.IGNORECASE)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", re.IGNORECASE | re.DOTALL)
_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_NAMED_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]{1,31});")
_XML_PREDEFINED = {"amp", "lt", "gt", "quot", "apos"}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}
_SELF_CLOSING_RE = re.compile(r"<([A-Za-z][\w:.-]*)(\s[^<>]*?)?/>")


def _decode(data: bytes) -> str:
    """Decode XML bytes honouring BOMs and the XML declaration's encoding."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    head = data[:200]
    m = re.match(rb"^\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9_.-]+)[\"']", head)
    enc = m.group(1).decode("ascii") if m else "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def parse_xml(data: bytes) -> etree._Element:
    """Parse a data-only XML part (OOXML/OPF/container). DOCTYPE and entities are refused."""
    text = _decode(data)
    if _ENTITY_DECL_RE.search(text):
        raise UnsafeXMLError("XML entity declarations are not allowed")
    if "<!DOCTYPE" in text.upper():
        raise UnsafeXMLError("DOCTYPE declarations are not allowed")
    text = _XML_DECL_RE.sub("", text, count=1)
    try:
        return etree.fromstring(text.encode("utf-8"), parser=_strict_parser())
    except etree.XMLSyntaxError as exc:
        raise UnsafeXMLError(f"invalid XML: {exc}") from exc


def _replace_named_entities(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in _XML_PREDEFINED:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        if cp is None:
            value = html.entities.html5.get(name + ";")
            if value is None:
                return m.group(0)
            return "".join(f"&#{ord(c)};" for c in value)
        return f"&#{cp};"

    return _NAMED_ENTITY_RE.sub(repl, text)


def _expand_self_closing(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        tag = m.group(1)
        if tag.lower() in _VOID_TAGS:
            return m.group(0)
        attrs = m.group(2) or ""
        return f"<{tag}{attrs}></{tag}>"

    return _SELF_CLOSING_RE.sub(repl, text)


def parse_xhtml(data: bytes) -> etree._Element:
    """Parse an (X)HTML content document safely and return the root element."""
    text = _decode(data)
    if _ENTITY_DECL_RE.search(text):
        raise UnsafeXMLError("XML entity declarations are not allowed")
    m = _DOCTYPE_RE.search(text[:4096])
    if m:
        if m.group(1):
            raise UnsafeXMLError("DOCTYPE with internal subset is not allowed")
        text = text[: m.start()] + text[m.end():]
    elif "<!DOCTYPE" in text.upper():
        raise UnsafeXMLError("unexpected DOCTYPE declaration")
    text = _XML_DECL_RE.sub("", text, count=1)
    text = _replace_named_entities(text)
    try:
        return etree.fromstring(text.encode("utf-8"), parser=_strict_parser())
    except etree.XMLSyntaxError:
        pass
    parser = lxml_html.HTMLParser(
        recover=True, no_network=True, remove_comments=True, remove_pis=True, encoding="utf-8"
    )
    try:
        root = lxml_html.document_fromstring(_expand_self_closing(text).encode("utf-8"), parser=parser)
    except (etree.ParserError, ValueError) as exc:
        raise UnsafeXMLError(f"invalid XHTML: {exc}") from exc
    if root is None:
        raise UnsafeXMLError("empty document")
    return root


def localname(tag: object) -> str:
    """Element tag without namespace, lowercase ('' for comments/PIs)."""
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    if ":" in tag:
        tag = tag.rsplit(":", 1)[1]
    return tag.lower()
