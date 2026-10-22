"""Housekeeping: storage cleanup and health checks."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from .. import logs, settings
from ..config import paths
from ..db import save, session
from ..jobs.queue import JobContext, task
from ..media import encoders, ffmpeg_bin
from ..models import Account, Clip, Job, Post, Video, utcnow

log = logs.get("ops")


def _rm(p: str) -> int:
    try:
        path = Path(p)
        if path.exists():
            n = path.stat().st_size
            path.unlink()
            return n
    except OSError:
        pass
    return 0


@task("cleanup", lane="light")
def cleanup(ctx: JobContext, p: dict) -> None:
    now = utcnow()
    freed = 0
    keep_src = timedelta(days=int(settings.get("keep_source_days")))
    keep_rej = timedelta(days=int(settings.get("keep_rejected_days")))
    keep_pub = timedelta(days=int(settings.get("keep_published_days")))
    with session() as s:
        videos = s.exec(select(Video).where(Video.source_deleted == False, Video.status.in_(("ready", "skipped")))).all()  # noqa: E712
    for v in videos:
        if now - v.updated_at < keep_src:
            continue
        with session() as s:
            open_clips = s.exec(select(Clip).where(Clip.video_id == v.id,
                                                   Clip.status.in_(("rendering", "review", "approved")))).all()
        if open_clips:
            continue  # still being reviewed: re-renders need the original
        freed += _rm(v.file_path)
        wav = paths().work / str(v.id) / "audio16k.wav"
        freed += _rm(str(wav))
        masters = paths().work / str(v.id) / "masters"
        if masters.exists():
            freed += sum(f.stat().st_size for f in masters.glob("*") if f.is_file())
            shutil.rmtree(masters, ignore_errors=True)
        v.source_deleted = True
        save(v)
    with session() as s:
        clips = s.exec(select(Clip)).all()
    for c in clips:
        age = now - (c.updated_at or c.created_at)
        if (c.status in ("rejected", "qc_failed", "failed") and age > keep_rej) or (c.status == "published" and age > keep_pub):
            freed += _rm(c.file_path)
            freed += _rm((c.meta or {}).get("base_path", ""))
            c.file_path = ""
            save(c)
    for f in paths().tmp.glob("*"):
        try:
            if now.timestamp() - f.stat().st_mtime > 86400:
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
        except OSError:
            pass
    with session() as s:
        old_jobs = s.exec(select(Job).where(Job.status.in_(("done", "canceled")), Job.finished_at < now - timedelta(days=14))).all()
        for j in old_jobs:
            s.delete(j)
        s.commit()
    log.info("cleanup freed %.1f MB", freed / 1e6)
    settings.set("last_cleanup", {"at": now.isoformat(), "freed_mb": round(freed / 1e6, 1)})


def disk_usage() -> dict:
    home = paths().home
    def size(p: Path) -> int:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0
    total, used, free = shutil.disk_usage(home)
    return {"library_mb": round(size(paths().library) / 1e6), "clips_mb": round(size(paths().clips) / 1e6),
            "work_mb": round(size(paths().work) / 1e6), "disk_free_gb": round(free / 1e9, 1)}


def health() -> dict:
    checks = []

    def add(name, ok, detail="", level="error"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "level": "ok" if ok else level})

    try:
        ffmpeg_bin()
        from ..media import HW_ENCODERS, encoder_works

        hw = [e.split("_")[1] for e in HW_ENCODERS if encoder_works(e)]
        add("ffmpeg", True, ("hardware encoding: " + ", ".join(hw)) if hw else "software encoding (libx264)")
    except RuntimeError as exc:
        add("ffmpeg", False, str(exc))
    hb = settings.get("worker_heartbeat")
    alive = False
    if hb:
        try:
            alive = (utcnow() - datetime.fromisoformat(hb)).total_seconds() < 60
        except ValueError:
            pass
    add("worker", alive, "running" if alive else "not running: start with `clipforge up`")
    try:
        from ..understand.transcribe import _engine

        eng = _engine()
        add("transcription", True, f"{settings.get('whisper_model').split('/')[-1]} ({'MLX' if 'mlx' in eng.__name__ else 'faster-whisper'})")
    except RuntimeError as exc:
        add("transcription", False, str(exc))
    from .. import llm

    if llm.backend() == "server":
        ok = llm.available()
        add("local LLM", ok, f"{settings.get('llm_server_model')} via {settings.get('llm_server_url')}" if ok else
            "no local LLM server running: install Ollama and run `ollama pull qwen3:8b`", level="warning")
    else:
        add("local LLM", llm.available(), settings.get("llm_model") if llm.available() else "disabled or mlx-lm missing",
            level="warning")
    du = disk_usage()
    add("disk", du["disk_free_gb"] > 10, f"{du['disk_free_gb']} GB free", level="warning")
    with session() as s:
        accounts = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
        failed_posts = s.exec(select(Post).where(Post.status == "failed")).all()
    expired = [a.name for a in accounts if a.status == "expired"]
    add("pages", not expired, "all connected" if not expired else "reconnect: " + ", ".join(expired), level="warning")
    add("publishing", not failed_posts, "no failures" if not failed_posts else f"{len(failed_posts)} failed posts",
        level="warning")
    from ..notify import telegram

    add("telegram", telegram.configured(), "connected" if telegram.configured() else "not set up (optional)",
        level="info")
    return {"ok": all(c["ok"] for c in checks if c["level"] == "error" or c["ok"]), "checks": checks, "disk": du}
