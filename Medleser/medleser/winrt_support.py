"""Keep Windows' COM multi-threaded apartment (MTA) alive for the whole process.

WinRT objects (voices, OCR engines, activation factories cached by pywinrt)
live in the MTA. The web server runs requests on short-lived worker threads;
when the last thread in the MTA exits, Windows tears the apartment down and
any cached WinRT object becomes invalid – the next call crashes the process
(access violation). ``CoIncrementMTAUsage`` keeps the MTA alive independently
of thread lifetimes, and threads that have not initialised COM then join it
implicitly.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading

_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_cookie = None


def ensure_mta() -> bool:
    """Idempotent. Returns True when the MTA is (now) pinned for the process."""
    global _cookie
    if sys.platform != "win32":
        return False
    from .runtime_fix import preload_cpp_runtime

    preload_cpp_runtime()  # before pywinrt can load its older msvcp140.dll
    if _cookie is not None:
        return True
    with _lock:
        if _cookie is not None:
            return True
        cookie = ctypes.c_void_p()
        try:
            hr = ctypes.windll.ole32.CoIncrementMTAUsage(ctypes.byref(cookie))
        except (AttributeError, OSError) as exc:  # very old Windows
            _LOG.warning("CoIncrementMTAUsage unavailable: %s", exc)
            return False
        if hr != 0:
            _LOG.warning("CoIncrementMTAUsage failed: 0x%08X", hr & 0xFFFFFFFF)
            return False
        _cookie = cookie
        return True
