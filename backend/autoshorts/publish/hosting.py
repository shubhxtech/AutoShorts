"""Temporary public URLs for Instagram (Instagram Login can only fetch videos from a URL).

Prefers a configured public base URL (your own tunnel). Otherwise it starts a
free Cloudflare quick tunnel for the few minutes Instagram needs, then shuts
it down. Links are signed and expire, and only rendered clips can be served.

The tunnel never points at the dashboard: it points at a separate, tiny server
(`media_server_port`) whose only route is the signed clip link. If you run your
own tunnel (public_base_url), point it at that port, shown in the worker log.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import shutil
import subprocess
import threading
import time

from .. import logs, settings

log = logs.get("hosting")


def _secret() -> bytes:
    s = settings.get("media_secret")
    if not s:
        s = secrets.token_hex(32)
        settings.set("media_secret", s)
    return s.encode()


def sign(clip_id: int, ttl_s: int = 3600) -> str:
    exp = int(time.time()) + ttl_s
    msg = f"{clip_id}.{exp}".encode()
    sig = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{clip_id}.{exp}.{sig}"


def verify(token: str) -> int | None:
    try:
        cid, exp, sig = token.split(".")
        msg = f"{cid}.{exp}".encode()
        good = hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:32]
        if hmac.compare_digest(good, sig) and int(exp) >= time.time():
            return int(cid)
    except ValueError:
        pass
    return None


_media_port: int | None = None
_media_lock = threading.Lock()


def media_app():
    """An app with exactly one route: the signed, expiring clip link."""
    from pathlib import Path

    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse

    from ..db import session
    from ..models import Clip

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/public/media/{token}")
    def public_media(token: str):
        cid = verify(token.removesuffix(".mp4"))
        if cid is None:
            raise HTTPException(403)
        with session() as s:
            clip = s.get(Clip, cid)
        if not clip or not clip.file_path or not Path(clip.file_path).is_file():
            raise HTTPException(404)
        return FileResponse(clip.file_path, media_type="video/mp4")

    return app


def media_server_port() -> int:
    """Start the media-only server once per process (on a free local port) and return its port."""
    global _media_port
    with _media_lock:
        if _media_port is not None:
            return _media_port
        import socket

        import uvicorn

        port = int(settings.get("public_media_port") or 0)
        if not port:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(media_app(), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, name="media-server", daemon=True).start()
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        _media_port = port
        log.info("public media server on 127.0.0.1:%d (serves signed clip links only)", port)
        return port


class Tunnel:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None

    def __enter__(self) -> Tunnel:
        base = settings.get("public_base_url")
        if base:
            self.url = base.rstrip("/")
            return self
        exe = shutil.which("cloudflared") or ("/opt/homebrew/bin/cloudflared" if shutil.os.path.exists("/opt/homebrew/bin/cloudflared") else None)
        if not exe:
            raise RuntimeError("Instagram Login accounts need a public URL for the video. Install cloudflared "
                               "(macOS `brew install cloudflared`, Windows `winget install Cloudflare.cloudflared`) or connect the account with Facebook Login, which uploads directly.")
        port = media_server_port()
        self.proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        found = threading.Event()

        def reader():
            for line in self.proc.stdout:  # type: ignore[union-attr]
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m and not self.url:
                    self.url = m.group(0)
                    found.set()

        threading.Thread(target=reader, daemon=True).start()
        if not found.wait(45):
            self.__exit__(None, None, None)
            raise RuntimeError("cloudflared did not produce a tunnel URL")
        time.sleep(3)  # let DNS propagate
        log.info("tunnel up at %s", self.url)
        return self

    def media_url(self, clip_id: int) -> str:
        return f"{self.url}/public/media/{sign(clip_id)}.mp4"

    def __exit__(self, *exc) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
