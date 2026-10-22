"""FastAPI application: REST API, media files and the built dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .. import __version__, config, logs
from ..db import init_db, session
from ..logs import setup_logging
from ..models import Clip
from .routes import router
from .security import SecurityMiddleware, set_cookie, token

setup_logging("api")
init_db()
token()          # create the access token now, so the macOS app and dev server can read it right away

app = FastAPI(title="AutoShorts", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.add_middleware(SecurityMiddleware)          # outermost: host check + access token
app.include_router(router)
log = logs.get("api")


@app.get("/api/ping", include_in_schema=False)
def ping():
    return {"ok": True}


@app.exception_handler(Exception)
async def _errors(request: Request, exc: Exception):
    from ..rights import RightsError

    if isinstance(exc, RightsError):
        return JSONResponse({"detail": f"Rights check failed: {exc}"}, status_code=400)
    # Details stay in the log; the client only gets a reference to find them.
    import secrets as _secrets

    ref = _secrets.token_hex(3)
    log.exception("unhandled error %s on %s %s", ref, request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": f"Something went wrong (ref {ref}). The details are in the Activity log."},
                        status_code=500)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


@app.get("/media/clips/{rest:path}")
def clip_media(rest: str):
    root = config.paths().clips
    path = root / rest
    if not _inside(path, root) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else None,
                        headers={"Cache-Control": "no-cache"})


@app.get("/media/faceless/{vid}/{name}")
def faceless_media(vid: int, name: str):
    root = config.paths().work / str(vid)
    path = root / name
    if not _inside(path, root) or path.suffix not in (".png", ".jpg", ".wav") or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/media/voice/{name}")
def voice_media(name: str):
    root = config.paths().tmp / "voice_previews"
    path = root / name
    if not _inside(path, root) or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/public/media/{token}")
def public_media(token: str):
    """Signed, expiring link used only while Instagram fetches a reel."""
    from ..publish.hosting import verify

    cid = verify(token.removesuffix(".mp4"))
    if cid is None:
        raise HTTPException(403)
    with session() as s:
        clip = s.get(Clip, cid)
    if not clip or not clip.file_path or not Path(clip.file_path).is_file():
        raise HTTPException(404)
    return FileResponse(clip.file_path, media_type="video/mp4")


_dist = config.frontend_dist()


@app.get("/{rest:path}", include_in_schema=False)
def spa(rest: str):
    if rest.startswith(("api/", "media/", "public/")):
        raise HTTPException(404)
    if _dist is None:
        page = HTMLResponse("<h2>AutoShorts API is running.</h2><p>Build the dashboard with "
                            "<code>make web</code> (or run <code>npm run dev</code> in frontend/).</p>")
        set_cookie(page)
        return page
    target = _dist / rest
    if rest and _inside(target, _dist) and target.is_file():
        return FileResponse(target)
    page = FileResponse(_dist / "index.html", headers={"Cache-Control": "no-cache"})
    set_cookie(page)            # the dashboard's own requests now carry the access token
    return page
