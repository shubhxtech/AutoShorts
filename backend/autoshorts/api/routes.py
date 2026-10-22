"""REST API used by the dashboard and the Mac app."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from .. import config, rights, settings
from ..db import get_session, save
from ..jobs.queue import cancel as cancel_job
from ..jobs.queue import enqueue
from ..media import probe
from ..models import (
    RIGHTS_TYPES,
    SOURCE_KINDS,
    Account,
    Clip,
    Comment,
    Discovery,
    Event,
    Job,
    MetricSnapshot,
    Moment,
    Post,
    Source,
    Video,
    utcnow,
)
from .summary import today_summary

router = APIRouter(prefix="/api")


def media_url(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        rel = p.relative_to(config.paths().clips)
        return f"/media/clips/{rel.as_posix()}?v={int(p.stat().st_mtime) if p.exists() else 0}"
    except ValueError:
        return ""


def _dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def dump(obj, exclude: set[str] | None = None) -> dict:
    d = obj.model_dump(exclude=exclude or set())
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------- overview

@router.get("/health")
def health():
    from ..ops.tasks import health as h

    return h()


@router.get("/summary")
def summary():
    s = today_summary()
    s["paused"] = bool(settings.get("automation_paused"))
    return s


@router.post("/automation")
def automation(paused: bool = Body(..., embed=True)):
    settings.set("automation_paused", paused)
    return {"paused": paused}


@router.get("/events")
def list_events(limit: int = 50, unread: bool = False, since_id: int = 0, s: Session = Depends(get_session)):
    q = select(Event).where(Event.id > since_id).order_by(col(Event.id).desc()).limit(limit)
    if unread:
        q = q.where(Event.read == False)  # noqa: E712
    return [dump(e) for e in s.exec(q).all()]


@router.post("/events/read")
def read_events(s: Session = Depends(get_session)):
    for e in s.exec(select(Event).where(Event.read == False)).all():  # noqa: E712
        e.read = True
        s.add(e)
    s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- settings

@router.get("/settings")
def get_settings():
    out = settings.public_settings()
    out["_paths"] = {"home": str(config.paths().home), "music": str(config.paths().music),
                     "fonts": str(config.paths().fonts)}
    out["_oauth_redirects"] = {"youtube": f"{config.base_url()}/api/oauth/youtube/callback",
                               "instagram": f"{settings.get('public_base_url') or config.base_url()}/api/oauth/instagram/callback"}
    return out


@router.put("/settings")
def put_settings(values: dict[str, Any] = Body(...)):
    settings.update(values)
    return settings.public_settings()


@router.post("/settings/youtube-client")
async def upload_youtube_client(project: str = Query("default"), file: UploadFile = File(...)):
    try:
        cfg = json.loads(await file.read())
    except ValueError as exc:
        raise HTTPException(400, "Not a JSON file") from exc
    if not (cfg.get("web") or cfg.get("installed")):
        raise HTTPException(400, "Expected a Google OAuth client_secret.json (web or desktop app)")
    secrets_ = dict(settings.get("youtube_client_secrets") or {})
    secrets_[project] = cfg
    settings.set("youtube_client_secrets", secrets_)
    return {"ok": True, "projects": list(secrets_)}


@router.get("/models")
def local_models():
    """Local model presets that fit Apple-silicon Macs."""
    import os

    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    def have(repo: str) -> bool:
        return (cache / ("models--" + repo.replace("/", "--"))).exists()
    llms = [
        {"id": "mlx-community/Qwen3.5-9B-MLX-4bit", "size_gb": 6.0, "note": "Best quality on 16 GB Macs (default)"},
        {"id": "mlx-community/Qwen3.5-4B-MLX-4bit", "size_gb": 3.1, "note": "Faster, lighter; good for 8 GB Macs"},
        {"id": "mlx-community/gemma-4-e4b-it-4bit", "size_gb": 5.2, "note": "Strong multilingual alternative"},
    ]
    whisper = [
        {"id": "mlx-community/whisper-large-v3-turbo", "size_gb": 1.6, "note": "Best accuracy/speed (default)"},
        {"id": "mlx-community/whisper-small-mlx", "size_gb": 0.5, "note": "Fast drafts"},
    ]
    for m in llms + whisper:
        m["downloaded"] = have(m["id"])
    return {"llm": llms, "whisper": whisper}


# ---------------------------------------------------------------- sources (rights registry)

class SourceIn(BaseModel):
    name: str
    kind: str = "youtube_channel"
    url: str = ""
    extra_urls: list[str] = []
    rights_type: str = "own"
    proof_kind: str = "email"
    proof_url: str = ""
    proof_notes: str = ""
    allowed_platforms: list[str] = ["youtube", "instagram"]
    credit_text: str = ""
    credit_link: str = ""
    revenue_terms: str = ""
    payout_per_1k: float | None = None
    expires_at: datetime | None = None
    active: bool = True
    niche: str = ""
    language: str = "auto"
    caption_script: str = "native"
    watch: bool = True
    watch_interval_hours: int = 6
    auto_ingest: bool = True
    min_viral_score: float = 1.5
    max_video_age_days: int = 30
    max_ingest_per_check: int = 2
    min_duration_s: int = 300
    cta_keyword: str = ""
    auto_publish: bool = False
    auto_publish_top: int = 3
    target_accounts: list[int] = []


def _source_out(src: Source, s: Session) -> dict:
    d = dump(src)
    d["rights"] = rights.badge(src)
    vids = s.exec(select(Video).where(Video.source_id == src.id)).all()
    d["video_count"] = len(vids)
    d["processed_count"] = sum(1 for v in vids if v.status not in ("discovered",))
    return d


@router.get("/sources")
def list_sources(s: Session = Depends(get_session)):
    return [_source_out(x, s) for x in s.exec(select(Source).order_by(col(Source.id).desc())).all()]


@router.get("/sources/meta")
def source_meta():
    return {"kinds": SOURCE_KINDS, "rights_types": RIGHTS_TYPES}


@router.post("/sources")
def create_source(body: SourceIn, s: Session = Depends(get_session)):
    if body.kind not in SOURCE_KINDS or body.rights_type not in RIGHTS_TYPES:
        raise HTTPException(400, "Unknown source kind or rights type")
    src = save(Source(**_aware(body.model_dump())), s)
    if src.watch:
        enqueue("watch_source", {"source_id": src.id}, dedupe_key=f"watch:{src.id}")
    return _source_out(src, s)


def _aware(d: dict) -> dict:
    v = d.get("expires_at")
    if isinstance(v, datetime) and v.tzinfo is None:
        d["expires_at"] = v.replace(tzinfo=UTC)
    return d


@router.put("/sources/{sid}")
def update_source(sid: int, body: SourceIn, s: Session = Depends(get_session)):
    src = s.get(Source, sid)
    if not src:
        raise HTTPException(404)
    for k, v in _aware(body.model_dump()).items():
        setattr(src, k, v)
    return _source_out(save(src, s), s)


@router.delete("/sources/{sid}")
def delete_source(sid: int, s: Session = Depends(get_session)):
    src = s.get(Source, sid)
    if not src:
        raise HTTPException(404)
    src.active = False
    src.watch = False
    save(src, s)
    return {"ok": True}


@router.post("/sources/{sid}/proof")
async def upload_proof(sid: int, file: UploadFile = File(...), s: Session = Depends(get_session)):
    src = s.get(Source, sid)
    if not src:
        raise HTTPException(404)
    safe = "".join(ch for ch in (file.filename or "proof") if ch.isalnum() or ch in "._-")[-80:]
    dest = config.paths().proofs / f"{sid}_{secrets.token_hex(4)}_{safe}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    src.proof_file = str(dest)
    return _source_out(save(src, s), s)


@router.post("/sources/{sid}/check")
def check_source_now(sid: int):
    enqueue("watch_source", {"source_id": sid}, priority=3, dedupe_key=f"watch:{sid}")
    return {"ok": True}


# ---------------------------------------------------------------- paste a link

class QuickIn(BaseModel):
    url: str
    rights_type: str = "own"
    attest: bool = False
    proof_url: str = ""
    proof_notes: str = ""
    credit_text: str = ""
    credit_link: str = ""
    niche: str = ""
    language: str = "auto"
    caption_script: str = "native"
    platforms: list[str] = ["youtube", "instagram"]
    accounts: list[int] = []
    auto_publish: bool = False
    auto_publish_top: int = 3
    cta_keyword: str = ""
    preset: str = ""


def _quick_meta(url: str) -> dict:
    """Title/channel/thumbnail for a link, quickly (best effort)."""
    try:
        import yt_dlp

        from ..ingest.listing import ytdlp_opts

        with yt_dlp.YoutubeDL(ytdlp_opts(skip_download=True, extract_flat=False, socket_timeout=15)) as y:
            info = y.extract_info(url, download=False) or {}
        return {"title": info.get("title", ""), "channel": info.get("channel") or info.get("uploader", ""),
                "channel_url": info.get("channel_url", ""), "thumbnail": info.get("thumbnail", ""),
                "duration": info.get("duration") or 0, "views": info.get("view_count") or 0, "id": info.get("id", "")}
    except Exception:
        return {}


MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}


def _check_local_media(path: Path) -> None:
    """Local imports must be a video or audio file, and never something inside the app's own data folder."""
    p = path.resolve()
    if not p.is_file() or p.suffix.lower() not in MEDIA_EXTS:
        raise HTTPException(400, "That path isn't a video or audio file")
    try:
        p.relative_to(config.paths().home.resolve())
        raise HTTPException(400, "Files inside the AutoShorts data folder can't be imported")
    except ValueError:
        pass


@router.post("/quick")
def quick_add(body: QuickIn, s: Session = Depends(get_session)):
    """Paste a video link: register its rights, then find, edit (and optionally post) the best clips."""
    url = body.url.strip()
    if not url:
        raise HTTPException(400, "Paste a video link")
    if not body.attest:
        raise HTTPException(400, "Confirm that you own this video or have permission to clip it")
    if body.rights_type not in RIGHTS_TYPES:
        raise HTTPException(400, "Unknown rights type")
    local = Path(url).expanduser()
    is_file = local.exists()
    if is_file:
        _check_local_media(local)
    existing = s.exec(select(Video).where(Video.url == (str(local) if is_file else url),
                                          col(Video.status).notin_(("deleted", "canceled", "failed")))).first()
    if existing:
        if existing.status in ("discovered", "skipped"):
            existing.status = "queued"
            save(existing, s)
            enqueue("process_video", {"video_id": existing.id}, priority=10, dedupe_key=f"process:{existing.id}")
        return {"source": _source_out(s.get(Source, existing.source_id), s), "video": _video_out(existing, s),
                "duplicate": True}
    meta = {} if is_file else _quick_meta(url)
    proof_notes = body.proof_notes or f"Rights confirmed by the account owner in the dashboard on {utcnow():%Y-%m-%d}"
    kind = "direct_url" if is_file else ("youtube_video" if ("youtube.com" in url or "youtu.be" in url) else "direct_url")
    name = meta.get("channel") or meta.get("title") or local.stem or url
    src = save(Source(name=name[:120], kind=kind, url=str(local) if is_file else url, rights_type=body.rights_type,
                      proof_kind="campaign_link" if body.rights_type == "clipping_campaign" else "other",
                      proof_url=body.proof_url, proof_notes=proof_notes, allowed_platforms=body.platforms,
                      credit_text=body.credit_text or (meta.get("channel") or ""), credit_link=body.credit_link,
                      niche=body.niche, language=body.language, caption_script=body.caption_script, watch=False,
                      auto_ingest=False, auto_publish=body.auto_publish, auto_publish_top=body.auto_publish_top,
                      cta_keyword=body.cta_keyword.strip(),
                      target_accounts=body.accounts), s)
    video = save(Video(source_id=src.id, external_id=meta.get("id") or (f"file:{local}" if is_file else url),
                       url=str(local) if is_file else url, title=meta.get("title") or local.stem,
                       channel=meta.get("channel", ""), thumbnail=meta.get("thumbnail", ""),
                       duration_s=float(meta.get("duration") or 0), view_count=int(meta.get("views") or 0),
                       status="queued"), s)
    enqueue("process_video", {"video_id": video.id}, priority=10, dedupe_key=f"process:{video.id}")
    return {"source": _source_out(src, s), "video": _video_out(video, s)}


# ---------------------------------------------------------------- videos / episodes

def _video_out(v: Video, s: Session, detail: bool = False) -> dict:
    d = dump(v, exclude={"description"} if not detail else set())
    if v.status in ("queued", "rendering"):
        mine = _video_jobs(s, v.id)
        if mine and all(j.status == "queued" for j in mine):
            ahead = len(s.exec(select(Job).where(Job.lane == "heavy", col(Job.status).in_(("queued", "running")),
                                                 Job.id < min(j.id for j in mine))).all())
            if ahead:
                d["stage_message"] = f"waiting in line · {ahead} job{'s' if ahead > 1 else ''} ahead"
    src = s.get(Source, v.source_id)
    d["source_name"] = src.name if src else ""
    d["rights"] = rights.badge(src)
    clips = s.exec(select(Clip).where(Clip.video_id == v.id)).all()
    d["clip_counts"] = {st: sum(1 for c in clips if c.status == st) for st in {c.status for c in clips}}
    if not d.get("thumbnail"):
        d["thumbnail"] = _local_thumb(v)
    return d


def _local_thumb(v: Video) -> str:
    """A frame from the downloaded video (or the first drawn scene) when the platform gave no thumbnail."""
    work = config.paths().work / str(v.id)
    scene = work / "scene_00.png"
    if scene.exists():
        return f"/media/faceless/{v.id}/scene_00.png"
    thumb = work / "thumb.jpg"
    if not thumb.exists() and v.file_path and Path(v.file_path).exists() and v.duration_s:
        from ..media import ffmpeg

        try:
            work.mkdir(parents=True, exist_ok=True)
            ffmpeg("-ss", f"{min(v.duration_s * 0.1, 60):.1f}", "-i", v.file_path, "-frames:v", "1", "-vf", "scale=320:-2",
                   str(thumb))
        except Exception:
            return ""
    return f"/media/faceless/{v.id}/thumb.jpg" if thumb.exists() else ""


@router.get("/videos")
def list_videos(status: str | None = None, source_id: int | None = None, limit: int = 100,
                s: Session = Depends(get_session)):
    q = select(Video).order_by(col(Video.id).desc()).limit(limit)
    if status:
        q = q.where(col(Video.status).in_(status.split(",")))
    else:
        q = q.where(col(Video.status).notin_(("deleted", "discovered")))
    if source_id:
        q = q.where(Video.source_id == source_id)
    return [_video_out(v, s) for v in s.exec(q).all()]


@router.get("/videos/{vid}")
def get_video(vid: int, s: Session = Depends(get_session)):
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    d = _video_out(v, s, detail=True)
    d["moments"] = [dump(m) for m in s.exec(select(Moment).where(Moment.video_id == vid)
                                            .order_by(col(Moment.final_score).desc())).all()]
    return d


@router.get("/videos/{vid}/transcript")
def video_transcript(vid: int, q: str = "", s: Session = Depends(get_session)):
    v = s.get(Video, vid)
    if not v or not v.timeline_path:
        raise HTTPException(404, "Not transcribed yet")
    tl = json.loads(Path(v.timeline_path).read_text())
    sents = tl["sentences"]
    if q:
        sents = [x for x in sents if q.lower() in x["text"].lower()]
    return {"duration": tl["duration"], "sentences": [{k: x[k] for k in ("i", "s", "e", "text", "energy", "laughs",
                                                                         "speaker") if k in x} for x in sents],
            "energy": tl["energy"][::5], "hop": tl["hop"] * 5, "laughs": tl.get("laughs", [])}


@router.post("/videos/{vid}/process")
def process_video(vid: int, s: Session = Depends(get_session)):
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    chk = rights.check_source(s.get(Source, v.source_id))
    if not chk.ok:
        raise HTTPException(400, "Rights check failed: " + "; ".join(chk.problems))
    v.status, v.error = "queued", ""
    save(v, s)
    enqueue("process_video", {"video_id": vid}, priority=5, dedupe_key=f"process:{vid}")
    return {"ok": True}


@router.post("/videos/{vid}/skip")
def skip_video(vid: int, s: Session = Depends(get_session)):
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    v.status = "skipped"
    save(v, s)
    return {"ok": True}


def _video_jobs(s: Session, vid: int) -> list[Job]:
    moment_ids = {m.id for m in s.exec(select(Moment).where(Moment.video_id == vid)).all()}
    clip_ids = {c.id for c in s.exec(select(Clip).where(Clip.video_id == vid)).all()}
    jobs = s.exec(select(Job).where(col(Job.status).in_(("queued", "running")))).all()
    return [j for j in jobs if (j.kind == "process_video" and j.payload.get("video_id") == vid)
            or (j.kind == "render_moment" and j.payload.get("moment_id") in moment_ids)
            or (j.kind == "rerender_clip" and j.payload.get("clip_id") in clip_ids)]


@router.post("/videos/{vid}/cancel")
def cancel_video(vid: int, s: Session = Depends(get_session)):
    """Stop everything AutoShorts is doing for this video (download, transcription, rendering)."""
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    n = 0
    for j in _video_jobs(s, vid):
        n += int(cancel_job(j.id))
    for c in s.exec(select(Clip).where(Clip.video_id == vid, Clip.status == "rendering")).all():
        c.status, c.error = "failed", "canceled"
        s.add(c)
    s.commit()
    v = s.get(Video, vid)
    v.status, v.stage_message = "canceled", "canceled by you"
    save(v, s)
    return {"ok": True, "jobs_canceled": n}


class RemineIn(BaseModel):
    retranscribe: bool = False
    language: str | None = None          # auto | en | hi
    caption_script: str | None = None    # native (Devanagari) | latin (Hinglish)


@router.post("/videos/{vid}/remine")
def remine_video(vid: int, body: RemineIn | None = None, s: Session = Depends(get_session)):
    """Find clips again with the latest miner (keeps the download; drops unposted clips).
    With retranscribe, the transcript is redone too (e.g. to fix the language or caption script)."""
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    body = body or RemineIn()
    src = s.get(Source, v.source_id)
    if src and (body.language or body.caption_script):
        if body.language:
            src.language = body.language
        if body.caption_script:
            src.caption_script = body.caption_script
        save(src, s)
    if body.retranscribe or body.language or body.caption_script:
        for name in ("transcript.json", "timeline.json"):
            (config.paths().work / str(vid) / name).unlink(missing_ok=True)
    for j in _video_jobs(s, vid):
        cancel_job(j.id)
    for c in s.exec(select(Clip).where(Clip.video_id == vid)).all():
        if s.exec(select(Post).where(Post.clip_id == c.id, col(Post.status).in_(("published", "publishing")))).first():
            continue
        for p in (c.file_path, c.thumb_path):
            if p:
                Path(p).unlink(missing_ok=True)
        for post in s.exec(select(Post).where(Post.clip_id == c.id)).all():
            s.delete(post)
        s.delete(c)
    for m in s.exec(select(Moment).where(Moment.video_id == vid)).all():
        if not s.exec(select(Clip).where(Clip.moment_id == m.id)).first():
            s.delete(m)
    s.commit()
    v = s.get(Video, vid)
    v.status, v.error, v.stage_message = "queued", "", "finding clips again"
    save(v, s)
    enqueue("process_video", {"video_id": vid}, priority=9, dedupe_key=f"process:{vid}")
    return {"ok": True}


@router.delete("/videos/{vid}")
def delete_video(vid: int, s: Session = Depends(get_session)):
    """Cancel and remove a video, its files and its unpublished clips."""
    v = s.get(Video, vid)
    if not v:
        raise HTTPException(404)
    for j in _video_jobs(s, vid):
        cancel_job(j.id)
    for c in s.exec(select(Clip).where(Clip.video_id == vid)).all():
        if s.exec(select(Post).where(Post.clip_id == c.id, Post.status == "published")).first():
            continue
        for p in (c.file_path, c.thumb_path):
            if p:
                Path(p).unlink(missing_ok=True)
        for post in s.exec(select(Post).where(Post.clip_id == c.id)).all():
            s.delete(post)
        s.delete(c)
    s.commit()
    if v.file_path:
        Path(v.file_path).unlink(missing_ok=True)
    shutil.rmtree(config.paths().work / str(vid), ignore_errors=True)
    v.status, v.source_deleted, v.file_path = "deleted", True, ""
    save(v, s)
    return {"ok": True}


@router.post("/moments/custom")
def custom_moment(video_id: int = Body(...), start: float = Body(...), end: float = Body(...),
                  hook: str = Body(""), s: Session = Depends(get_session)):
    """Clip a hand-picked range (from the transcript view)."""
    v = s.get(Video, video_id)
    if not v or not v.transcript_path:
        raise HTTPException(404)
    rights.require(s.get(Source, v.source_id))
    tl = json.loads(Path(v.timeline_path).read_text())
    text = " ".join(x["text"] for x in tl["sentences"] if x["s"] >= start - 0.1 and x["e"] <= end + 0.1)
    m = save(Moment(video_id=video_id, start_s=start, end_s=end, text=text, title=hook or text[:60],
                    hooks=[{"text": hook or text[:60], "style": "bold_claim"}], status="selected",
                    final_score=6.0, llm_score=6.0, meta={}), s)
    enqueue("render_moment", {"moment_id": m.id}, priority=8, dedupe_key=f"render:{m.id}")
    return dump(m)


# ---------------------------------------------------------------- clips (review queue)

def _clip_out(c: Clip, s: Session) -> dict:
    d = dump(c)
    d["url"] = media_url(c.file_path)
    base = (c.meta or {}).get("base_path")
    d["base_url"] = media_url(base) if base and Path(base).exists() else ""
    d["overlay_spec"] = (c.render_stats or {}).get("overlay")
    d["overlay"] = (c.meta or {}).get("overlay") or {}
    from ..pipeline import needs_burn

    d["needs_burn"] = needs_burn(c)
    d["thumb_url"] = media_url(c.thumb_path)
    clean = (c.render_stats or {}).get("thumb_clean") or ""
    d["base_poster"] = media_url(clean) if clean and Path(clean).exists() else ""
    src = s.get(Source, c.source_id)
    v = s.get(Video, c.video_id)
    m = s.get(Moment, c.moment_id)
    d["rights"] = rights.badge(src)
    d["source_name"] = src.name if src else ""
    d["video_title"] = v.title if v else ""
    d["faceless"] = bool(m and (m.meta or {}).get("faceless"))
    wv = config.paths().work / str(c.video_id) / "voice.txt"
    d["voice"] = wv.read_text().strip() if d["faceless"] and wv.exists() else ""
    d["moment"] = {"title": m.title, "scores": m.scores, "final_score": m.final_score, "reason": m.reason,
                   "hooks": m.hooks, "text": m.text, "start_s": m.start_s, "end_s": m.end_s,
                   "punchline_s": m.punchline_s} if m else None
    d["posts"] = [dump(p) for p in s.exec(select(Post).where(Post.clip_id == c.id)).all()]
    stats = d.get("render_stats") or {}
    d["render_stats"] = {k: v for k, v in stats.items() if k not in ("words",)}
    d["words"] = stats.get("words", [])
    return d


@router.get("/clips")
def list_clips(status: str | None = "review", video_id: int | None = None, limit: int = 200,
               s: Session = Depends(get_session)):
    q = select(Clip)
    if status:
        q = q.where(col(Clip.status).in_(status.split(",")))
    if video_id:
        q = q.where(Clip.video_id == video_id)
    q = q.order_by(col(Clip.predicted_score).desc(), col(Clip.id).desc()).limit(limit)
    return [_clip_out(c, s) for c in s.exec(q).all()]


@router.get("/clips/{cid}")
def get_clip(cid: int, s: Session = Depends(get_session)):
    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    d = _clip_out(c, s)
    d["siblings"] = [{"id": x.id, "variant": x.variant, "status": x.status, "thumb_url": media_url(x.thumb_path)}
                     for x in s.exec(select(Clip).where(Clip.moment_id == c.moment_id, Clip.id != c.id)).all()]
    return d


class ClipEdit(BaseModel):
    hook_text: str | None = None
    caption_style: str | None = None
    opening: str | None = None
    edit_preset: str | None = None
    layout: str | None = None
    caption_edits: dict[str, str] | None = None
    cut: list[list[float]] | None = None   # virtual cut: ordered [src_start, src_end] ranges ([] = none)
    trim_start: float | None = None    # seconds added to the start (negative extends)
    trim_end: float | None = None      # seconds added to the end (negative shortens)
    youtube_title: str | None = None
    instagram_caption: str | None = None
    overlay: dict[str, Any] | None = None   # live text edits: no re-render, burned on approval
    rerender: bool = True


@router.patch("/clips/{cid}")
def edit_clip(cid: int, body: ClipEdit, s: Session = Depends(get_session)):
    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    meta = dict(c.meta or {})
    needs_render = False
    live = bool(meta.get("base_path"))
    ov = dict(meta.get("overlay") or {})
    if body.overlay is not None:
        ov.update({k: v for k, v in body.overlay.items()})
    if body.hook_text is not None and body.hook_text != c.hook_text:
        c.hook_text = body.hook_text
        if live:
            ov["hook"] = body.hook_text
        else:
            needs_render = True
    if body.caption_style and body.caption_style != c.caption_style:
        c.caption_style = body.caption_style
        if live:
            ov["caption_style"] = body.caption_style
        else:
            needs_render = True
    if body.opening and body.opening != c.opening:
        c.opening, needs_render = body.opening, True
    if body.edit_preset and body.edit_preset != c.edit_preset:
        c.edit_preset, needs_render = body.edit_preset, True
    if body.layout:
        meta["layout_override"], needs_render = body.layout, True
    if body.caption_edits is not None:
        c.caption_edits = {str(k): v for k, v in body.caption_edits.items()}
        if live:
            ov["caption_edits"] = c.caption_edits
        else:
            needs_render = True
    if body.trim_start is not None or body.trim_end is not None:
        trim = dict(meta.get("trim") or {})
        if body.trim_start is not None:
            trim["start"] = body.trim_start
        if body.trim_end is not None:
            trim["end"] = body.trim_end
        meta["trim"], needs_render = trim, True
    if body.cut is not None:
        # Instant: the player plays the draft in this order; the final cut is assembled on approve.
        meta["cut"] = [[round(float(a), 3), round(float(b), 3)] for a, b in body.cut if float(b) - float(a) > 0.04]
        if not meta["cut"]:
            meta.pop("cut")
    if body.youtube_title is not None:
        meta["youtube_title"] = body.youtube_title
    if body.instagram_caption is not None:
        meta["instagram_caption"] = body.instagram_caption
    meta["overlay"] = ov
    c.meta = meta
    save(c, s)
    if needs_render and body.rerender:
        c.status = "rendering"
        save(c, s)
        enqueue("rerender_clip", {"clip_id": cid}, priority=10, dedupe_key=f"rerender:{cid}")
    return _clip_out(c, s)


class Decision(BaseModel):
    ids: list[int]
    accounts: list[int] = []
    at: datetime | None = None


@router.post("/clips/approve")
def approve(body: Decision, s: Session = Depends(get_session)):
    from ..publish.schedule import auto_targets, schedule_clip

    out = []
    for cid in body.ids:
        c = s.get(Clip, cid)
        if not c or c.status not in ("review", "approved", "qc_failed"):
            continue
        chk = rights.check_source(s.get(Source, c.source_id))
        if not chk.ok:
            raise HTTPException(400, f"Clip {cid}: " + "; ".join(chk.problems))
        c.status, c.reviewed_at = "approved", utcnow()
        c.target_accounts = body.accounts
        save(c, s)
        from ..pipeline import needs_burn

        if needs_burn(c):
            enqueue("burn_clip", {"clip_id": c.id}, priority=9, dedupe_key=f"burn:{c.id}")
        targets = body.accounts or (auto_targets(c) if settings.get("auto_schedule_on_approve") else [])
        at = body.at.replace(tzinfo=UTC) if body.at and body.at.tzinfo is None else body.at
        posts = schedule_clip(c, targets, at) if targets else []
        out.append({"clip_id": cid, "posts": [dump(p) for p in posts]})
    return out


@router.post("/clips/reject")
def reject(body: Decision, s: Session = Depends(get_session)):
    for cid in body.ids:
        c = s.get(Clip, cid)
        if c and c.status in ("review", "approved", "qc_failed", "failed"):
            c.status, c.reviewed_at = "rejected", utcnow()
            save(c, s)
    return {"ok": True}


@router.post("/clips/{cid}/export")
def export_clip(cid: int, s: Session = Depends(get_session)):
    """Make (or refresh) the final MP4 with the current text edits."""
    from ..pipeline import needs_burn

    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    if needs_burn(c):
        enqueue("burn_clip", {"clip_id": cid}, priority=10, dedupe_key=f"burn:{cid}")
        return {"ready": False}
    return {"ready": True, "url": media_url(c.file_path)}


@router.get("/system/memory")
def system_memory():
    """What the worker has loaded, its RAM use, and machine totals."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        machine = {"total_gb": round(vm.total / 1e9, 1), "used_pct": vm.percent}
    except Exception:
        machine = {}
    return {"worker": settings.get("worker_memory") or {}, "last_free": settings.get("last_free_memory") or {},
            "idle_minutes": settings.get("unload_idle_minutes"), "machine": machine}


@router.post("/system/free-memory")
def free_memory():
    """Ask the worker to drop its cached models (now if idle, otherwise right after the current job)."""
    settings.set("free_memory_request", utcnow().isoformat())
    return {"ok": True}


FILMSTRIP_FRAMES = 24


@router.get("/clips/{cid}/filmstrip")
def clip_filmstrip(cid: int, s: Session = Depends(get_session)):
    """Thumbnails across the clip in one image (FILMSTRIP_FRAMES wide), for the editor timeline."""
    from fastapi.responses import FileResponse

    from ..media import ffmpeg

    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    src = (c.meta or {}).get("base_path") or c.file_path
    if not src or not Path(src).is_file():
        raise HTTPException(404, "This clip has no video yet")
    out = Path(src).with_suffix(".strip.jpg")
    if not out.exists() or out.stat().st_mtime < Path(src).stat().st_mtime:
        dur = max(0.5, float(c.duration_s or 1))
        ffmpeg("-i", src, "-vf", f"fps={FILMSTRIP_FRAMES / dur:.4f},scale=-2:112,tile={FILMSTRIP_FRAMES}x1",
               "-frames:v", "1", "-q:v", "5", str(out))
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/clips/{cid}/titles")
def clip_titles(cid: int, s: Session = Depends(get_session)):
    """Ask the local LLM for 10 title options (arrive in clip.meta.title_options)."""
    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    c.meta = {k: v for k, v in (c.meta or {}).items() if k != "title_options"}
    s.add(c)
    s.commit()
    enqueue("suggest_titles", {"clip_id": cid}, priority=12, dedupe_key=f"titles:{cid}")
    return {"ok": True}


@router.post("/clips/{cid}/rerender")
def rerender(cid: int, s: Session = Depends(get_session)):
    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    c.status = "rendering"
    save(c, s)
    enqueue("rerender_clip", {"clip_id": cid}, priority=10, dedupe_key=f"rerender:{cid}")
    return _clip_out(c, s)


@router.post("/clips/{cid}/restore")
def restore(cid: int, s: Session = Depends(get_session)):
    c = s.get(Clip, cid)
    if not c:
        raise HTTPException(404)
    c.status = "review"
    save(c, s)
    return _clip_out(c, s)


# ---------------------------------------------------------------- pages (accounts)

def _account_out(a: Account, s: Session) -> dict:
    d = dump(a, exclude={"credentials"})
    d["login"] = a.credentials.get("host", "google") if a.credentials else ""
    posts = s.exec(select(Post).where(Post.account_id == a.id, Post.status == "published")).all()
    d["published"] = len(posts)
    d["views"] = sum(p.views for p in posts)
    d["followers_gained"] = sum(p.followers_gained for p in posts)
    d["earnings"] = round(sum(p.earnings for p in posts), 2)
    d["scheduled"] = len(s.exec(select(Post).where(Post.account_id == a.id, Post.status == "scheduled")).all())
    if a.platform == "youtube":
        from ..publish.youtube import quota_left

        d["quota_left"] = quota_left(a.google_project)
    return d


@router.get("/accounts")
def list_accounts(s: Session = Depends(get_session)):
    return [_account_out(a, s) for a in s.exec(select(Account).order_by(col(Account.id))).all()]


class AccountEdit(BaseModel):
    name: str | None = None
    niche: str | None = None
    language: str | None = None
    voice: str | None = None
    cadence_per_day: int | None = None
    time_slots: list[str] | None = None
    timezone: str | None = None
    min_gap_minutes: int | None = None
    learn_slots: bool | None = None
    privacy_status: str | None = None
    default_hashtags: str | None = None
    google_project: str | None = None
    auto_reply_categories: list[str] | None = None
    auto_hide_spam: bool | None = None
    dm_funnels: list[dict] | None = None
    active: bool | None = None


@router.patch("/accounts/{aid}")
def edit_account(aid: int, body: AccountEdit, s: Session = Depends(get_session)):
    a = s.get(Account, aid)
    if not a:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    return _account_out(save(a, s), s)


@router.delete("/accounts/{aid}")
def remove_account(aid: int, s: Session = Depends(get_session)):
    a = s.get(Account, aid)
    if not a:
        raise HTTPException(404)
    a.active, a.credentials = False, {}
    for p in s.exec(select(Post).where(Post.account_id == aid, Post.status == "scheduled")).all():
        p.status = "canceled"
        s.add(p)
    save(a, s)
    return {"ok": True}


@router.get("/oauth/youtube/start")
def youtube_start(project: str = "default"):
    from ..publish import youtube

    try:
        return RedirectResponse(youtube.start_auth(project))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/oauth/youtube/callback")
def youtube_callback(state: str, code: str = "", error: str = ""):
    from ..publish import youtube

    if error:
        return RedirectResponse(f"/pages?error={error}")
    try:
        accs = youtube.finish_auth(state, code)
    except Exception as exc:
        return RedirectResponse(f"/pages?error={str(exc)[:200]}")
    return RedirectResponse(f"/pages?connected={len(accs)}")


@router.post("/accounts/instagram")
def instagram_token(access_token: str = Body(..., embed=True), ig_user_id: str = Body("", embed=True),
                    s: Session = Depends(get_session)):
    from ..publish import instagram

    try:
        accs = instagram.connect_with_token(access_token.strip(), ig_user_id.strip())
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return [_account_out(a, s) for a in accs]


_ig_states: set[str] = set()


@router.get("/oauth/instagram/start")
def instagram_start():
    from ..publish import instagram

    state = secrets.token_urlsafe(16)
    _ig_states.add(state)
    redirect = f"{settings.get('public_base_url') or config.base_url()}/api/oauth/instagram/callback"
    try:
        return RedirectResponse(instagram.oauth_url(redirect, state))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/oauth/instagram/callback")
def instagram_callback(code: str = "", state: str = "", error: str = ""):
    from ..publish import instagram

    if error or state not in _ig_states:
        return RedirectResponse(f"/pages?error={error or 'invalid state'}")
    _ig_states.discard(state)
    redirect = f"{settings.get('public_base_url') or config.base_url()}/api/oauth/instagram/callback"
    try:
        accs = instagram.oauth_finish(code, redirect)
    except Exception as exc:
        return RedirectResponse(f"/pages?error={str(exc)[:200]}")
    return RedirectResponse(f"/pages?connected={len(accs)}")


# ---------------------------------------------------------------- posts (schedule)

def _post_out(p: Post, s: Session) -> dict:
    d = dump(p)
    c = s.get(Clip, p.clip_id)
    a = s.get(Account, p.account_id)
    d["thumb_url"] = media_url(c.thumb_path) if c else ""
    d["clip_url"] = media_url(c.file_path) if c else ""
    d["hook_text"] = c.hook_text if c else ""
    d["account_name"] = a.name if a else ""
    d["account_avatar"] = a.avatar if a else ""
    return d


@router.get("/posts")
def list_posts(status: str | None = None, account_id: int | None = None, days: int = 30, limit: int = 300,
               s: Session = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    q = select(Post).where((Post.scheduled_at >= since) | (Post.status == "scheduled"))
    if status:
        q = q.where(col(Post.status).in_(status.split(",")))
    if account_id:
        q = q.where(Post.account_id == account_id)
    q = q.order_by(col(Post.scheduled_at)).limit(limit)
    return [_post_out(p, s) for p in s.exec(q).all()]


class PostEdit(BaseModel):
    scheduled_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    caption: str | None = None


@router.patch("/posts/{pid}")
def edit_post(pid: int, body: PostEdit, s: Session = Depends(get_session)):
    p = s.get(Post, pid)
    if not p or p.status not in ("scheduled", "failed"):
        raise HTTPException(400, "Only scheduled or failed posts can be edited")
    data = body.model_dump(exclude_none=True)
    if "scheduled_at" in data and data["scheduled_at"].tzinfo is None:
        data["scheduled_at"] = data["scheduled_at"].replace(tzinfo=UTC)
    for k, v in data.items():
        setattr(p, k, v)
    return _post_out(save(p, s), s)


@router.post("/posts/{pid}/cancel")
def cancel_post(pid: int, s: Session = Depends(get_session)):
    p = s.get(Post, pid)
    if not p or p.status not in ("scheduled", "failed"):
        raise HTTPException(400, "Can't cancel this post")
    p.status = "canceled"
    save(p, s)
    c = s.get(Clip, p.clip_id)
    if c and not s.exec(select(Post).where(Post.clip_id == c.id, Post.status.in_(("scheduled", "published")))).first():
        c.status = "approved"
        save(c, s)
    return {"ok": True}


@router.post("/posts/{pid}/now")
def publish_now(pid: int, s: Session = Depends(get_session)):
    p = s.get(Post, pid)
    if not p or p.status not in ("scheduled", "failed"):
        raise HTTPException(400, "Can't publish this post")
    p.status, p.scheduled_at, p.error = "scheduled", utcnow(), ""
    save(p, s)
    enqueue("publish_post", {"post_id": pid}, priority=9, dedupe_key=f"publish:{pid}")
    return {"ok": True}


@router.post("/posts/{pid}/earnings")
def set_earnings(pid: int, amount: float = Body(..., embed=True), s: Session = Depends(get_session)):
    p = s.get(Post, pid)
    if not p:
        raise HTTPException(404)
    p.earnings, p.earnings_manual = amount, True
    save(p, s)
    return {"ok": True}


# ---------------------------------------------------------------- analytics

@router.get("/analytics")
def analytics(days: int = 30, s: Session = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    posts = s.exec(select(Post).where(Post.status == "published", Post.published_at >= since)).all()
    clips = {c.id: c for c in s.exec(select(Clip).where(col(Clip.id).in_([p.clip_id for p in posts] or [0]))).all()}
    accounts = {a.id: a for a in s.exec(select(Account)).all()}
    sources = {x.id: x for x in s.exec(select(Source)).all()}

    def group(key):
        out: dict[str, dict] = {}
        for p in posts:
            k = key(p)
            if k is None:
                continue
            g = out.setdefault(str(k), {"posts": 0, "views": 0, "views_72h": [], "followers": 0, "earnings": 0.0,
                                        "avg_view_pct": []})
            g["posts"] += 1
            g["views"] += p.views
            g["followers"] += p.followers_gained
            g["earnings"] += p.earnings
            if p.views_72h is not None:
                g["views_72h"].append(p.views_72h)
            if p.avg_view_pct:
                g["avg_view_pct"].append(p.avg_view_pct)
        for g in out.values():
            v = sorted(g.pop("views_72h"))
            g["median_72h"] = v[len(v) // 2] if v else None
            r = g.pop("avg_view_pct")
            g["avg_view_pct"] = round(sum(r) / len(r), 1) if r else None
            g["earnings"] = round(g["earnings"], 2)
        return out

    daily: dict[str, dict] = {}
    for p in posts:
        snaps = s.exec(select(MetricSnapshot).where(MetricSnapshot.post_id == p.id)).all()
        for sn in snaps:
            day = sn.taken_at.date().isoformat()
            daily.setdefault(day, {})[p.id] = sn.views
    series = [{"day": d, "views": sum(v.values())} for d, v in sorted(daily.items())]
    top = sorted(posts, key=lambda p: -p.views)[:10]
    losers = sorted([p for p in posts if p.views_72h is not None], key=lambda p: p.views_72h)[:10]
    return {
        "totals": {"posts": len(posts), "views": sum(p.views for p in posts),
                   "followers": sum(p.followers_gained for p in posts),
                   "earnings": round(sum(p.earnings for p in posts), 2)},
        "by_page": {accounts[int(k)].name if int(k) in accounts else k: v
                    for k, v in group(lambda p: p.account_id).items()},
        "by_source": {sources[int(k)].name if int(k) in sources else k: v
                      for k, v in group(lambda p: clips[p.clip_id].source_id if p.clip_id in clips else None).items()},
        "by_hook_style": group(lambda p: clips[p.clip_id].hook_style if p.clip_id in clips else None),
        "by_caption_style": group(lambda p: clips[p.clip_id].caption_style if p.clip_id in clips else None),
        "by_length": group(lambda p: f"{int(clips[p.clip_id].duration_s // 15 * 15)}–{int(clips[p.clip_id].duration_s // 15 * 15 + 15)}s"
                           if p.clip_id in clips else None),
        "by_hour": group(lambda p: p.slot_hour if p.slot_hour >= 0 else None),
        "by_opening": group(lambda p: clips[p.clip_id].opening if p.clip_id in clips else None),
        "series": series,
        "top": [_post_out(p, s) for p in top],
        "losers": [_post_out(p, s) for p in losers],
        "velocity": [{"post_id": p.id, "hook": clips[p.clip_id].hook_text if p.clip_id in clips else "",
                      "views_24h": p.views_24h, "views_72h": p.views_72h} for p in posts
                     if p.views_24h is not None][:50],
    }


@router.get("/optimizer")
def optimizer():
    from ..analytics import bandit
    from ..analytics.tasks import build_report
    from ..mining.miner import score_weights

    model = settings.get("predictor") or {}
    return {"bandit": bandit.table("global"), "report": settings.get("weekly_report") or build_report(7),
            "model": {k: model.get(k) for k in ("samples", "r2", "top_features")},
            "score_weights": score_weights(), "last_learn": settings.get("last_learn")}


@router.post("/optimizer/run")
def run_optimizer():
    enqueue("learn", {}, priority=5, dedupe_key="learn")
    enqueue("weekly_report", {}, priority=4, dedupe_key="weekly_report")
    return {"ok": True}


# ---------------------------------------------------------------- comments

@router.get("/comments")
def list_comments(status: str = "drafted,new", limit: int = 200, s: Session = Depends(get_session)):
    q = select(Comment).where(col(Comment.reply_status).in_(status.split(","))).order_by(col(Comment.id).desc()).limit(limit)
    out = []
    for c in s.exec(q).all():
        d = dump(c)
        p = s.get(Post, c.post_id)
        a = s.get(Account, c.account_id)
        d["post_title"] = (p.title or p.caption[:80]) if p else ""
        d["post_url"] = p.url if p else ""
        d["account_name"] = a.name if a else ""
        out.append(d)
    return out


class CommentEdit(BaseModel):
    draft_reply: str | None = None
    reply_status: str | None = None


@router.patch("/comments/{cid}")
def edit_comment(cid: int, body: CommentEdit, s: Session = Depends(get_session)):
    c = s.get(Comment, cid)
    if not c:
        raise HTTPException(404)
    if body.draft_reply is not None:
        c.draft_reply = body.draft_reply
    if body.reply_status in ("approved", "skipped", "drafted"):
        c.reply_status = body.reply_status
    save(c, s)
    if c.reply_status == "approved":
        enqueue("post_replies", {}, priority=3, dedupe_key="post_replies")
    return dump(c)


@router.post("/comments/approve")
def approve_comments(ids: list[int] = Body(..., embed=True), s: Session = Depends(get_session)):
    for cid in ids:
        c = s.get(Comment, cid)
        if c and c.draft_reply and c.reply_status in ("drafted", "failed"):
            c.reply_status = "approved"
            s.add(c)
    s.commit()
    enqueue("post_replies", {}, priority=3, dedupe_key="post_replies")
    return {"ok": True}


@router.post("/comments/refresh")
def refresh_comments():
    enqueue("fetch_comments", {}, priority=3, dedupe_key="fetch_comments")
    return {"ok": True}


# ---------------------------------------------------------------- viral finder

@router.get("/discover")
def discover(s: Session = Depends(get_session), limit: int = 100):
    """Viral videos from your covered sources plus trend-scout leads."""
    vids = s.exec(select(Video).where(Video.status == "discovered").order_by(col(Video.viral_score).desc()).limit(limit)).all()
    leads = s.exec(select(Discovery).where(Discovery.status != "dismissed").order_by(col(Discovery.viral_score).desc()).limit(limit)).all()
    return {"covered": [_video_out(v, s) for v in vids],
            "leads": [{**dump(d), "covered_by": (s.get(Source, d.source_id).name if d.source_id and s.get(Source, d.source_id) else None)}
                      for d in leads],
            "queries": settings.get("scout_queries") or []}


@router.post("/discover/scout")
def run_scout(queries: list[str] = Body(..., embed=True)):
    qs = [q.strip() for q in queries if q.strip()]
    settings.set("scout_queries", qs)
    enqueue("trend_scout", {"queries": qs}, priority=4, dedupe_key="trend_scout")
    return {"ok": True}


@router.post("/discover/{did}/dismiss")
def dismiss_lead(did: int, s: Session = Depends(get_session)):
    d = s.get(Discovery, did)
    if d:
        d.status = "dismissed"
        save(d, s)
    return {"ok": True}


@router.post("/discover/{did}/queue")
def queue_lead(did: int, s: Session = Depends(get_session)):
    """Clip a lead. Only allowed when a rights record covers the channel."""
    d = s.get(Discovery, did)
    if not d:
        raise HTTPException(404)
    src = s.get(Source, d.source_id) if d.source_id else None
    chk = rights.check_source(src)
    if not chk.ok:
        raise HTTPException(400, "Add a rights record for this channel first (your own channel, a licence, written "
                                 "creator permission, or a clipping campaign that covers it).")
    v = s.exec(select(Video).where(Video.source_id == src.id, Video.external_id == d.external_id)).first()
    if v is None:
        v = save(Video(source_id=src.id, external_id=d.external_id, url=d.url, title=d.title, channel=d.channel,
                       channel_id=d.channel_id, thumbnail=d.thumbnail, published_at=d.published_at,
                       duration_s=d.duration_s, view_count=d.view_count, viral_score=d.viral_score,
                       status="queued"), s)
    d.status = "claimed"
    save(d, s)
    enqueue("process_video", {"video_id": v.id}, priority=5, dedupe_key=f"process:{v.id}")
    return {"ok": True, "video_id": v.id}


# ---------------------------------------------------------------- jobs

@router.get("/jobs")
def list_jobs(status: str = "queued,running,failed", limit: int = 100, s: Session = Depends(get_session)):
    q = select(Job).where(col(Job.status).in_(status.split(","))).order_by(col(Job.id).desc()).limit(limit)
    return [dump(j) for j in s.exec(q).all()]


@router.post("/jobs/{jid}/cancel")
def cancel(jid: int):
    return {"ok": cancel_job(jid)}


@router.post("/jobs/{jid}/retry")
def retry(jid: int, s: Session = Depends(get_session)):
    j = s.get(Job, jid)
    if not j or j.status not in ("failed", "canceled"):
        raise HTTPException(400)
    j.status, j.attempts, j.error, j.run_after = "queued", 0, "", utcnow()
    save(j, s)
    return {"ok": True}


# ---------------------------------------------------------------- faceless studio

class FacelessIn(BaseModel):
    topic: str = ""
    reference_url: str = ""
    style: str = "doodle"
    voice: str = "af_heart"
    seconds: int = 45
    language: str = "English"
    niche: str = ""
    cta_keyword: str = ""
    accounts: list[int] = []
    auto_publish: bool = False
    count: int = 1
    notes: str = ""
    grounding: bool = True
    script_text: str = ""
    format: str = "story"
    review_script: bool = False
    seed: int = 7


@router.get("/faceless/options")
def faceless_options():
    from dataclasses import asdict

    from ..faceless import SCRIPT_TEMPLATE, STYLES, VOICES
    from ..faceless_formats import FORMATS

    return {"styles": [{"id": k, "prompt": v} for k, v in STYLES.items()], "voices": VOICES, "template": SCRIPT_TEMPLATE,
            "formats": [{k: v for k, v in asdict(f).items() if k != "prompt"} for f in FORMATS.values()]}


@router.get("/faceless/projects")
def faceless_projects(s: Session = Depends(get_session)):
    """Faceless projects, newest first, with a cover image and their clips."""
    from ..faceless import load_project

    out = []
    for v in s.exec(select(Video).where(col(Video.external_id).startswith("faceless:"), Video.status != "deleted").order_by(col(Video.id).desc()).limit(60)).all():
        work = config.paths().work / str(v.id)
        proj = load_project(work)
        cover = next((f"/media/faceless/{v.id}/{p.name}" for p in sorted(work.glob("scene_*.png"))[:1]), "")
        clips = s.exec(select(Clip).where(Clip.video_id == v.id)).all()
        out.append({"id": v.id, "title": v.title, "status": v.status, "stage_message": v.stage_message,
                    "progress": v.progress, "format": proj.get("format", "story"), "style": proj.get("style", ""),
                    "voice": proj.get("voice", ""), "cover": cover, "created_at": v.created_at,
                    "clips": [{"id": c.id, "status": c.status, "variant": c.variant} for c in clips]})
    return out


class StoryboardIn(BaseModel):
    title: str | None = None
    hook: str | None = None
    character: str | None = None
    setting: str | None = None
    mood: str | None = None
    scenes: list[dict] | None = None
    style: str | None = None
    voice: str | None = None
    redraw: list[int] = []
    build: bool = True


def _storyboard(vid: int) -> dict:
    from ..faceless import image_paths, load_project, load_script

    work = config.paths().work / str(vid)
    if not (work / "script.json").exists():
        raise HTTPException(404, "No storyboard for this video")
    script = load_script(work)
    proj = load_project(work)
    vo = json.loads((work / "vo.json").read_text()) if (work / "vo.json").exists() else {}
    scenes = []
    for k, sc in enumerate(script.scenes):
        a, b = image_paths(work, k)
        d = None
        wav = work / f"vo_{k:02d}.wav"
        if wav.exists() and str(k) in vo:
            try:
                d = round(probe(wav).duration, 1)
            except Exception:
                d = None
        ver = int(a.stat().st_mtime) if a.exists() else 0
        scenes.append({**sc.model_dump(), "image": f"/media/faceless/{vid}/{a.name}?v={ver}" if a.exists() else "",
                       "image2": f"/media/faceless/{vid}/{b.name}?v={int(b.stat().st_mtime)}" if b.exists() else "",
                       "seconds": d})
    return {"video_id": vid, "project": proj, "script": {**script.model_dump(exclude={"scenes"})}, "scenes": scenes}


@router.get("/faceless/{vid}/storyboard")
def storyboard_get(vid: int):
    return _storyboard(vid)


@router.put("/faceless/{vid}/storyboard")
def storyboard_put(vid: int, body: StoryboardIn, s: Session = Depends(get_session)):
    """Save storyboard edits. Rebuilding re-voices only changed lines and redraws only changed (or chosen) shots."""
    from ..faceless import Scene, image_paths, load_project, load_script, save_project, save_script, tidy

    work = config.paths().work / str(vid)
    if not (work / "script.json").exists():
        raise HTTPException(404, "No storyboard for this video")
    script = load_script(work)
    for k in ("title", "hook", "character", "setting", "mood"):
        if getattr(body, k) is not None:
            setattr(script, k, getattr(body, k))
    if body.scenes is not None:
        try:
            script.scenes = [Scene.model_validate({k: v for k, v in sc.items() if k not in ("image", "image2", "seconds")})
                             for sc in body.scenes]
        except Exception as exc:
            raise HTTPException(400, f"Bad scene: {exc}") from exc
        if not script.scenes:
            raise HTTPException(400, "A video needs at least one scene")
    script = tidy(script)
    save_script(work, script)
    proj = load_project(work)
    if body.style:
        proj["style"] = body.style
    if body.voice:
        proj["voice"] = body.voice
    save_project(work, proj)
    meta_path = work / "images.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    for k in body.redraw:
        if 0 <= k < len(script.scenes):
            for p in image_paths(work, k):
                meta.pop(p.name, None)
            meta[f"bump:{k}"] = int(meta.get(f"bump:{k}", 0)) + 1     # a redraw asks for a different picture
    meta_path.write_text(json.dumps(meta, indent=1))
    if body.build:
        for c in s.exec(select(Clip).where(Clip.video_id == vid)).all():
            c.status = "rendering"
            s.add(c)
        v = s.get(Video, vid)
        if v:
            v.status, v.stage_message = "queued", "rebuilding from the storyboard"
            s.add(v)
        s.commit()
        enqueue("build_faceless", {"video_id": vid}, priority=10, dedupe_key=f"build_faceless:{vid}")
    return _storyboard(vid)


@router.post("/faceless/parse")
def faceless_parse(text: str = Body(..., embed=True)):
    """Preview how a script will be split into scenes."""
    from ..faceless import parse_script

    try:
        return parse_script(text)
    except Exception as exc:
        raise HTTPException(400, f"Couldn't read the script: {exc}") from exc


@router.post("/faceless")
def faceless_create(body: FacelessIn):
    from .. import faceless

    if not body.topic.strip() and not body.reference_url.strip() and not body.script_text.strip():
        raise HTTPException(400, "Give a topic, a script, or a reference video link")
    if body.script_text.strip():
        try:
            if not faceless.parse_script(body.script_text)["scenes"]:
                raise ValueError("no narration found")
        except Exception as exc:
            raise HTTPException(400, f"Couldn't read the script: {exc}") from exc
    vids = []
    for k in range(max(1, min(10, body.count))):
        v = faceless.create(topic=body.topic, reference_url=body.reference_url, style=body.style, voice=body.voice,
                            seconds=body.seconds, language=body.language, niche=body.niche,
                            cta_keyword=body.cta_keyword, accounts=body.accounts, auto_publish=body.auto_publish,
                            notes=body.notes, grounding=body.grounding, script_text=body.script_text,
                            fmt=body.format, review_script=body.review_script, seed=body.seed + k)
        vids.append(v.id)
    return {"videos": vids}


# ---------------------------------------------------------------- pipeline insights (works before anything is posted)

@router.get("/insights")
def insights(days: int = 14, s: Session = Depends(get_session)):
    since = utcnow() - timedelta(days=days)
    videos = s.exec(select(Video).where(Video.created_at >= since, col(Video.status).notin_(("discovered", "deleted")))).all()
    vids = {v.id for v in videos}
    moments = s.exec(select(Moment).where(col(Moment.video_id).in_(vids or {0}))).all()
    clips = s.exec(select(Clip).where(Clip.created_at >= since)).all()
    posts = s.exec(select(Post).where(Post.created_at >= since)).all()
    passed = [c for c in clips if c.qc_passed]
    funnel = [
        {"label": "Videos in", "value": len(videos)},
        {"label": "Moments found", "value": len(moments)},
        {"label": "Variants rendered", "value": len([c for c in clips if c.status != "rendering"]),
         "note": f"{len([c for c in clips if c.status != 'rendering']) / max(1, len(moments)):.1f} per moment"},
        {"label": "Passed quality checks", "value": len(passed)},
        {"label": "Approved", "value": len([c for c in clips if c.status in ("approved", "scheduled", "published")])},
        {"label": "Posted", "value": len([p for p in posts if p.status == "published"])},
    ]
    fails: dict[str, int] = {}
    for c in clips:
        for q in c.qc or []:
            if not q.get("passed") and q.get("blocking", True):
                fails[q["name"]] = fails.get(q["name"], 0) + 1
    bins = [0] * 10
    for c in clips:
        if c.status != "rendering":
            bins[min(9, max(0, int(c.predicted_score)))] += 1
    per_day: dict[str, list[int]] = {}
    for c in clips:
        d = c.created_at.date().isoformat()
        per_day.setdefault(d, [0, 0])
        per_day[d][0] += 1
        per_day[d][1] += int(c.qc_passed)
    mix: dict[str, int] = {}
    for c in passed:
        mix[c.edit_preset or "clean"] = mix.get(c.edit_preset or "clean", 0) + 1
    structures: dict[str, int] = {}
    for m in moments:
        st = (m.meta or {}).get("structure") or "other"
        structures[st] = structures.get(st, 0) + 1
    durations = [round(c.duration_s) for c in passed]
    return {
        "funnel": funnel,
        "qc_failures": sorted(({"label": k, "value": v} for k, v in fails.items()), key=lambda x: -x["value"]),
        "score_bins": [{"label": f"{i}–{i + 1}", "value": n} for i, n in enumerate(bins)],
        "per_day": [{"day": d, "clips": v[0], "passed": v[1]} for d, v in sorted(per_day.items())],
        "edit_mix": sorted(({"label": k, "value": v} for k, v in mix.items()), key=lambda x: -x["value"]),
        "structures": sorted(({"label": k, "value": v} for k, v in structures.items()), key=lambda x: -x["value"]),
        "avg_duration": round(sum(durations) / len(durations), 1) if durations else 0,
        "pass_rate": round(len(passed) / max(1, len([c for c in clips if c.status != "rendering"])) * 100),
    }


# ---------------------------------------------------------------- voices

@router.get("/voices")
def list_voices():
    import os

    from .. import voices

    cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    have = {e: (cache / ("models--" + repo.replace("/", "--"))).exists() for e, repo in voices.MODELS.items()}
    sizes = {"kokoro": 0.3, "qwen": 4.5, "design": 2.5, "chatterbox": 3.0, "clone": 4.5}
    return {"voices": voices.catalog(), "engines": {e: {"downloaded": have[e], "size_gb": sizes[e]} for e in voices.MODELS}}


@router.post("/voices/preview")
def voice_preview(voice: str = Body(...), text: str = Body("")):
    from .. import voices

    try:
        p = voices.preview(voice, text)
    except Exception as exc:
        raise HTTPException(400, f"Voice preview failed: {exc}") from exc
    finally:
        voices.unload()
    return {"url": f"/media/voice/{p.name}?v={int(p.stat().st_mtime)}"}


@router.post("/voices/upload")
async def voice_upload(name: str = Query(...), file: UploadFile = File(...)):
    """A 10-30 s clean sample of a voice you own (e.g. yours) for cloning."""
    from ..media import ffmpeg

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name)[:40] or "my_voice"
    d = config.paths().home / "voices"
    d.mkdir(exist_ok=True)
    raw = config.paths().tmp / f"upload_{safe}"
    with open(raw, "wb") as f:
        shutil.copyfileobj(file.file, f)
    ffmpeg("-i", str(raw), "-ac", "1", "-ar", "24000", "-t", "30", str(d / f"{safe}.wav"))
    raw.unlink(missing_ok=True)
    return {"id": f"chatterbox:clone:{safe}.wav"}


@router.post("/videos/{vid}/revoice")
def revoice(vid: int, voice: str = Body(..., embed=True), s: Session = Depends(get_session)):
    v = s.get(Video, vid)
    if not v or not (config.paths().work / str(vid) / "script.json").exists():
        raise HTTPException(400, "Only faceless videos can be re-voiced")
    for c in s.exec(select(Clip).where(Clip.video_id == vid)).all():
        c.status = "rendering"
        s.add(c)
    s.commit()
    enqueue("revoice_faceless", {"video_id": vid, "voice": voice}, priority=10, dedupe_key=f"revoice:{vid}")
    return {"ok": True}
