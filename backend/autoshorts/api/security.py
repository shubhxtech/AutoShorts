"""Who may talk to the engine.

The dashboard runs on your own machine, but a web page you visit could still try to reach it (through
DNS rebinding, or a public tunnel). So:

  * Host check: requests must be addressed to localhost (or hosts you list in AUTOSHORTS_ALLOWED_HOSTS).
  * Access token: every /api and /media request must carry the per-install token, either as the
    `autoshorts_token` cookie (set when the dashboard loads, HttpOnly + SameSite=Strict, so other sites
    can't use it) or the `X-AutoShorts-Token` header (the macOS app and the dev server).

The token lives in the data folder (secrets/api_token, readable only by you).
"""

from __future__ import annotations

import hmac
import os
import secrets
from functools import lru_cache

from starlette.responses import JSONResponse, PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .. import config

COOKIE = "autoshorts_token"
HEADER = "x-autoshorts-token"

# Reachable without the token: a liveness probe, and OAuth redirects that arrive from the
# provider in your normal browser (the callbacks validate their own state).
OPEN_PATHS = ("/api/ping",)
OPEN_PREFIXES = ("/api/oauth/",)


@lru_cache(maxsize=1)
def token() -> str:
    p = config.paths().secrets / "api_token"
    if p.exists() and p.read_text().strip():
        return p.read_text().strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    p.write_text(t)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return t


def allowed_hosts() -> set[str]:
    hosts = {"localhost", "127.0.0.1", "::1"}
    extra = os.environ.get("AUTOSHORTS_ALLOWED_HOSTS", "")
    hosts |= {h.strip().lower() for h in extra.split(",") if h.strip()}
    bind = config.host()
    if bind not in ("0.0.0.0", "::", ""):
        hosts.add(bind.lower())
    return hosts


def _hostname(raw: str) -> str:
    raw = raw.strip().lower()
    if raw.startswith("["):                       # [::1]:3000
        return raw[1:raw.find("]")] if "]" in raw else raw
    return raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw


def host_ok(raw: str) -> bool:
    h = _hostname(raw)
    return h in allowed_hosts() or h.endswith(".localhost")


def _cookie(header: str) -> str:
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return ""


def needs_token(path: str) -> bool:
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return False
    return path.startswith(("/api/", "/media/"))


class SecurityMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if not host_ok(headers.get("host", "")):
            await PlainTextResponse("Invalid host", status_code=400)(scope, receive, send)
            return
        if needs_token(scope.get("path", "")):
            given = headers.get(HEADER) or _cookie(headers.get("cookie", ""))
            if not given or not hmac.compare_digest(given, token()):
                await JSONResponse({"detail": "Not authorised. Open the dashboard from this computer."},
                                   status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def set_cookie(response) -> None:
    response.set_cookie(COOKIE, token(), httponly=True, samesite="strict", path="/")
