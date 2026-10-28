"""Logging to a small rotating file, with secret redaction.

Logs contain technical events only – never document text and never API
keys. As an extra safety net, anything that looks like a key or our session
token is masked before it is written.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    re.compile(r"sk_[A-Za-z0-9]{16,}"),  # ElevenLabs-style keys
    re.compile(r"(?i)(xi-api-key|api[_-]?key|authorization)(\W{1,4})[^\s,;\"']+"),
    re.compile(r"(?i)(token=)[A-Za-z0-9_\-]{16,}"),
]


class RedactingFilter(logging.Filter):
    def __init__(self, extra_secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.extra = [s for s in extra_secrets if s]

    def add_secret(self, secret: str) -> None:
        if secret:
            self.extra.append(secret)

    def redact(self, text: str) -> str:
        for secret in self.extra:
            text = text.replace(secret, "***")
        text = _PATTERNS[0].sub("sk_***", text)
        text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
        text = _PATTERNS[2].sub(lambda m: f"{m.group(1)}***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = self.redact(message)
        record.args = None
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


_FILTER = RedactingFilter()


class RedactingFormatter(logging.Formatter):
    """Redacts the final text, including tracebacks (filled in after filters have run)."""

    def format(self, record: logging.LogRecord) -> str:
        return _FILTER.redact(super().format(record))


def redaction_filter() -> RedactingFilter:
    return _FILTER


def setup_logging(log_dir: Path, *, debug: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "medleser.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(_FILTER)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "multipart", "pywebview", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
