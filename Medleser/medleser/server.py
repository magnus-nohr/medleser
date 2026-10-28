"""Local web backend (FastAPI) for the desktop window.

Security model
--------------
* Bound to 127.0.0.1 on a random free port – never reachable from the network.
* A random session token (generated at start-up, never written to disk or
  logs) is handed to the app window once via ``/start?token=…``; the server
  answers with an HttpOnly, SameSite=Strict cookie. Every other request must
  carry that cookie, so other local programs and web pages cannot use the API.
* The Host header must be ``127.0.0.1:<port>`` (blocks DNS-rebinding).
* State-changing requests must come from our own origin and carry the
  ``X-Medleser: 1`` header (blocks cross-site form posts).
* No CORS headers; strict Content-Security-Policy; no-store caching for API.
"""

from __future__ import annotations

import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import resource_dir
from .context import AppContext
from .errors import UserFacingError

_LOG = logging.getLogger(__name__)
COOKIE = "medleser_session"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)

_NOT_AUTHORIZED_HTML = """<!doctype html><html lang="nb"><meta charset="utf-8">
<title>Medleser</title><body style="font-family:Segoe UI,Arial,sans-serif;padding:2rem">
<h1>Medleser</h1><p>Denne siden kan bare åpnes fra Medleser-programmet.
Start Medleser fra Start-menyen eller skrivebordet.</p></body></html>"""


def web_dir() -> Path:
    return resource_dir() / "web"


def create_app(ctx: AppContext, *, testing: bool = False) -> FastAPI:
    app = FastAPI(title="Medleser", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = ctx

    def expected_host() -> str | None:
        return f"127.0.0.1:{ctx.port}" if ctx.port else None

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = request.headers.get("host", "")
        allowed = expected_host()
        if allowed is not None and host != allowed:
            return Response("Forbidden", status_code=403)
        path = request.url.path
        if path == "/start":
            token = request.query_params.get("token", "")
            if not hmac.compare_digest(token.encode(), ctx.token.encode()):
                return HTMLResponse(_NOT_AUTHORIZED_HTML, status_code=401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, ctx.token, httponly=True, samesite="strict", path="/",
                                secure=False)
            _LOG.info("UI session started")
            return _harden(response)
        cookie = request.cookies.get(COOKIE, "")
        if not hmac.compare_digest(cookie.encode(), ctx.token.encode()):
            if path.startswith("/api/"):
                return JSONResponse({"error": {"code": "unauthorized", "message": "Ikke tilgang.",
                                               "details": None}}, status_code=401)
            return HTMLResponse(_NOT_AUTHORIZED_HTML, status_code=401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if allowed is not None and origin and origin != f"http://{allowed}":
                return Response("Forbidden", status_code=403)
            if request.headers.get("x-medleser") != "1":
                return Response("Forbidden", status_code=403)
        response = await call_next(request)
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
        return _harden(response)

    @app.exception_handler(UserFacingError)
    async def user_error(_: Request, exc: UserFacingError):
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Never echo the submitted values (they may contain an API key or document text).
        summary = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('type', 'invalid')}" for err in exc.errors()
        )
        return JSONResponse({"error": {"code": "invalid_request",
                                       "message": "Forespørselen var ugyldig.",
                                       "details": summary[:500]}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        _LOG.exception("Unhandled error")
        return JSONResponse({"error": {"code": "internal",
                                       "message": "Noe gikk galt. Prøv igjen.",
                                       "details": type(exc).__name__}}, status_code=500)

    from .routes import router

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(web_dir()), html=True), name="web")
    return app


def _harden(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
