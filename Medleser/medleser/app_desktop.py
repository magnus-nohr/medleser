"""Desktop entry point: local server + native window (pywebview / WebView2)."""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from typing import Optional

from . import APP_NAME, __version__
from .config import AppPaths, resource_dir
from .logging_setup import redaction_filter, setup_logging

_LOG = logging.getLogger(__name__)
_MUTEX_HANDLE = None


def _message_box(text: str, title: str = APP_NAME) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # MB_ICONINFORMATION
    else:
        print(text)


def _single_instance() -> bool:
    """Return False if Medleser is already running for this user (and focus it)."""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\Medleser-single-instance")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return False
    return True


_CRASH_FILE = None


def _enable_crash_log(paths: AppPaths) -> None:
    """Write a stack trace to logs/crash.log if native code crashes (no document text is included)."""
    global _CRASH_FILE
    import faulthandler

    try:
        _CRASH_FILE = open(paths.logs / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_CRASH_FILE, all_threads=True)
    except OSError:
        pass


def _bind_socket(port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        # Prevent other processes from hijacking our port on Windows.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    sock.bind(("127.0.0.1", port))
    sock.set_inheritable(False)
    return sock


class LocalServer:
    def __init__(self, app, sock: socket.socket) -> None:
        import uvicorn

        config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="off",
                                timeout_graceful_shutdown=2, server_header=False, date_header=False)
        self.server = uvicorn.Server(config)
        self.sock = sock
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [sock]},
                                       name="medleser-server", daemon=True)

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.time() + timeout
        while not self.server.started:
            if time.time() > deadline or not self.thread.is_alive():
                raise RuntimeError("Local server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
        try:
            self.sock.close()
        except OSError:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    from .runtime_fix import preload_cpp_runtime

    preload_cpp_runtime()  # before winrt/onnxruntime are imported (see runtime_fix.py)
    parser = argparse.ArgumentParser(prog="medleser", description="Medleser – lydbøker og medlesing")
    parser.add_argument("--browser", action="store_true", help="Utviklermodus: åpne i nettleseren")
    parser.add_argument("--port", type=int, default=0, help="Utviklermodus: fast port")
    parser.add_argument("--data-dir", help="Annen datamappe (f.eks. for testing)")
    parser.add_argument("--debug", action="store_true", help="Utviklerverktøy og detaljert logg")
    parser.add_argument("--no-open", action="store_true", help="Utviklermodus: ikke åpne nettleser")
    parser.add_argument("--dev-token", help="Utviklermodus: fast start-nøkkel (minst 16 tegn)")
    parser.add_argument("--selftest", metavar="FIL", help="Kjør selvtest og skriv resultatet til FIL (JSON)")
    args = parser.parse_args(argv)

    if args.selftest:
        from pathlib import Path

        from .selftest import run as run_selftest

        os._exit(run_selftest(Path(args.selftest)))

    if args.data_dir:
        os.environ["MEDLESER_DATA_DIR"] = args.data_dir
    paths = AppPaths.from_env().ensure()
    setup_logging(paths.logs, debug=args.debug)
    _enable_crash_log(paths)
    _LOG.info("Medleser %s starting (python %s)", __version__, sys.version.split()[0])

    if not args.browser and not _single_instance():
        return 0

    from .context import build_context
    from .server import create_app
    from .winrt_support import ensure_mta

    ensure_mta()  # WinRT (Windows voices/OCR) is used from short-lived worker threads
    ctx = build_context(paths)
    dev_token = args.dev_token or os.environ.get("MEDLESER_DEV_TOKEN")
    if args.browser and dev_token and len(dev_token) >= 16:
        ctx.token = dev_token  # developer mode only: stable start URL for tooling
    redaction_filter().add_secret(ctx.token)
    sock = _bind_socket(args.port if args.browser else 0)
    ctx.port = sock.getsockname()[1]
    app = create_app(ctx)
    server = LocalServer(app, sock)
    try:
        server.start()
    except Exception:
        _LOG.exception("Server start failed")
        _message_box("Medleser kunne ikke starte. Prøv å starte programmet på nytt.")
        return 1
    start_url = f"http://127.0.0.1:{ctx.port}/start?token={ctx.token}"

    exit_code = 0
    try:
        if args.browser:
            print(f"Medleser kjører (utviklermodus): {start_url}", flush=True)
            if not args.no_open:
                webbrowser.open(start_url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            exit_code = _run_window(ctx, start_url, paths, debug=args.debug)
    finally:
        _LOG.info("Shutting down")
        ctx.close()
        server.stop()
        logging.shutdown()
    # Make sure no worker thread (e.g. a long synthesis call) keeps the process alive.
    os._exit(exit_code)


def _run_window(ctx, start_url: str, paths: AppPaths, *, debug: bool) -> int:
    try:
        import webview
    except Exception:  # noqa: BLE001
        _LOG.exception("pywebview import failed")
        _message_box("Medleser kunne ikke åpne programvinduet.")
        return 1
    window = webview.create_window(
        APP_NAME,
        start_url,
        width=1280,
        height=860,
        min_size=(900, 620),
        background_color="#F7F5F0",
        text_select=True,
        zoomable=True,
    )
    ctx.window = window
    ctx.desktop = True
    icon = resource_dir() / "assets" / "medleser.ico"
    try:
        webview.start(gui="edgechromium", private_mode=True, debug=debug,
                      storage_path=str(paths.root / "webview"),
                      icon=str(icon) if icon.exists() else None)
    except Exception:  # noqa: BLE001
        _LOG.exception("Window failed")
        _message_box(
            "Medleser trenger «Microsoft Edge WebView2 Runtime», som vanligvis følger med Windows 11. "
            "Installer den fra Microsoft og prøv igjen."
        )
        return 1
    return 0
