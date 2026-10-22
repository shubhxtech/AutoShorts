"""The automated pipeline: watch -> ingest -> understand -> mine -> render variants -> quality checks -> review."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sqlmodel import select

from . import logs, rights, settings
from .config import paths
from .db import save, session
from .ingest import listing, viral
from .ingest.download import download
from .jobs.queue import JobContext, enqueue, task
from .media import extract_audio, file_hash, probe
from .models import Clip, Job, Moment, Source, Video, utcnow
from .notify import events

log = logs.get("pipeline")


# ---------------------------------------------------------------- watching sources

@task("watch_source", lane="light", max_attempts=2)
def watch_source(ctx: JobContext, p: dict) -> None:
    with session() as s:
        src = s.get(Source, p["source_id"])
    if src is None:
        return
    ctx.progress(0.1, f"checking {src.name}")
    cands = listing.list_source(src, limit=40)
    viral.score(cands)
    now = utcnow()
    new_videos: list[Video] = []
    with session() as s:
        for c in cands:
            v = s.exec(select(Video).where(Video.source_id == src.id, Video.external_id == c.external_id)).first()
            if v is None:
                v = Video(source_id=src.id, external_id=c.external_id, url=c.url, title=c.title, channel=c.channel,
                          channel_id=c.channel_id, thumbnail=c.thumbnail, published_at=c.published_at,
                          duration_s=c.duration_s, view_count=c.views, viral_score=c.viral_score, velocity=c.velocity)
                s.add(v)
                new_videos.append(v)
            else:
                v.view_count, v.viral_score, v.velocity = c.views or v.view_count, c.viral_score, c.velocity
                v.thumbnail = v.thumbnail or c.thumbnail
                s.add(v)
        src.last_checked_at = now
        s.add(src)
        s.commit()
        for v in new_videos:
            s.refresh(v)

    if not src.auto_ingest or settings.get("automation_paused"):
        return
    if not rights.check_source(src).ok:
        events.emit("rights_blocked", f"{src.name}: not ingesting", "; ".join(rights.check_source(src).problems),
                    level="warning", source_id=src.id)
        return
    with session() as s:
        pool = s.exec(select(Video).where(Video.source_id == src.id, Video.status == "discovered")).all()
    has_views = any(v.view_count for v in pool)

    def eligible(v: Video) -> bool:
        if v.duration_s and v.duration_s < src.min_duration_s:
            return False
        if v.published_at and src.max_video_age_days and now - v.published_at > timedelta(days=src.max_video_age_days):
            return False
        return not (has_views and v.viral_score < src.min_viral_score)

    picks = sorted((v for v in pool if eligible(v)),
                   key=lambda v: (v.viral_score if has_views else 0, v.published_at or now), reverse=True)
    for v in picks[: max(1, src.max_ingest_per_check)]:
        v.status = "queued"
        save(v)
        enqueue("process_video", {"video_id": v.id}, dedupe_key=f"process:{v.id}")
        events.emit("ingest", f"Queued “{v.title[:70]}”",
                    f"{src.name} · viral score {v.viral_score:.1f}×" if has_views else src.name, video_id=v.id)


# ---------------------------------------------------------------- processing an episode

def _set(video_id: int, **kw) -> Video:
    with session() as s:
        v = s.get(Video, video_id)
        for k, val in kw.items():
            setattr(v, k, val)
        v.updated_at = utcnow()
        s.add(v)
        s.commit()
        s.refresh(v)
        return v


@task("process_video", lane="heavy", max_attempts=3)
def process_video(ctx: JobContext, p: dict) -> None:
    try:
        _process_video(ctx, p)
    except Exception as exc:
        # Show the failure on the video itself (the job will retry if it has attempts left).
        msg = f"{type(exc).__name__}: {exc}"
        if "Failed to resolve" in msg or "nodename nor servname" in msg:
            msg = "No internet connection while downloading; will retry automatically."
        _set(p["video_id"], status="failed", error=msg[:500])
        raise


def _process_video(ctx: JobContext, p: dict) -> None:
    vid = p["video_id"]
    with session() as s:
        video = s.get(Video, vid)
        src = s.get(Source, video.source_id) if video else None
    if video is None:
        return
    try:
        rights.require(src)
    except rights.RightsError as exc:
        _set(vid, status="skipped", error=f"rights: {exc}")
        raise
    work = paths().work / str(vid)
    work.mkdir(parents=True, exist_ok=True)

    def stage(name: str, lo: float, hi: float):
        def prog(f: float, msg: str = "") -> None:
            ctx.progress(lo + (hi - lo) * f, msg or name)
            _maybe_progress(vid, lo + (hi - lo) * f, msg or name)
        return prog

    # 1. Download
    from .ingest.download import _has_video

    needs_video = src.kind not in ("rss",) and video.url.startswith("http")
    if not video.file_path or not Path(video.file_path).exists() or (needs_video and not _has_video(Path(video.file_path))):
        video = _set(vid, status="downloading", error="")
        path = download(video, src, stage("downloading", 0.0, 0.2))
        h = file_hash(path)
        with session() as s:
            dup = s.exec(select(Video).where(Video.content_hash == h, Video.id != vid,
                                             Video.status.in_(("ready", "rendering")))).first()
        if dup:
            _set(vid, status="skipped", error=f"duplicate of video #{dup.id}", file_path=str(path), content_hash=h)
            return
        pr = probe(path)
        video = _set(vid, file_path=str(path), content_hash=h, duration_s=pr.duration or video.duration_s,
                     width=pr.width, height=pr.height, fps=pr.fps, title=video.title or path.stem,
                     description=video.description, channel=video.channel, view_count=video.view_count)

    # 2. Transcribe (on-device Whisper)
    wav = work / "audio16k.wav"
    if not wav.exists():
        extract_audio(video.file_path, wav)
    transcript_path = work / "transcript.json"
    if not transcript_path.exists():
        _set(vid, status="transcribing")
        from .understand.transcribe import diarize, transcribe

        lang = None if (src.language or "auto") == "auto" else src.language
        data = transcribe(wav, transcript_path, language=lang, progress=stage("transcribing", 0.2, 0.5))
        data = diarize(wav, data)
        from .understand import punctuate

        if punctuate.needs_restoring(data["words"]):
            data["words"] = punctuate.restore(data["words"], progress=stage("restoring sentences", 0.5, 0.55))
            from .understand.transcribe import build_sentences as _bs

            data["sentences"] = _bs(data["words"])
        if src.caption_script == "latin" and data.get("language") == "hi":
            from .understand.script import romanise_words
            from .understand.transcribe import build_sentences

            data["words"] = romanise_words(data["words"])
            data["sentences"] = build_sentences(data["words"])
        transcript_path.write_text(json.dumps(data, ensure_ascii=False))
        video = _set(vid, transcript_path=str(transcript_path), language=data.get("language", ""))
    else:
        data = json.loads(transcript_path.read_text())
        from .understand import punctuate
        from .understand.transcribe import build_sentences

        if punctuate.needs_restoring(data["words"]) and not any(w.get("eos") for w in data["words"]):
            _set(vid, status="analyzing", stage_message="restoring sentences")
            data["words"] = punctuate.restore(data["words"], progress=stage("restoring sentences", 0.5, 0.55))
        data["sentences"] = build_sentences(data["words"])     # re-split with the latest rules
        transcript_path.write_text(json.dumps(data, ensure_ascii=False))
        video = _set(vid, transcript_path=str(transcript_path), language=data.get("language", ""))
    if len(data.get("words", [])) < 40:
        _set(vid, status="skipped", error="almost no speech found")
        return

    # 3. Understand
    _set(vid, status="analyzing")
    from .understand.signals import build_timeline

    timeline_path = work / "timeline.json"
    build_timeline(wav, data, timeline_path)
    video = _set(vid, timeline_path=str(timeline_path))

    # 4. Mine moments (local LLM)
    _set(vid, status="mining")
    from .mining.miner import mine

    moments = mine(video, src, stage("finding viral moments", 0.55, 0.95))
    if not moments:
        _set(vid, status="ready", error="no strong moments found")
        events.emit("clips_ready", f"No strong moments in “{video.title[:60]}”", level="warning", video_id=vid)
        return

    # 5. Precise word timing for the chosen clips (captions on the word, cuts between syllables)
    if settings.get("forced_alignment"):
        ctx.progress(0.96, "aligning words precisely")
        try:
            from .understand.align import align_ranges

            st = align_ranges(wav, transcript_path, [(m.start_s - 3.5, m.end_s) for m in moments], video.language)
            log.info("forced alignment: %s", st)
        except Exception as exc:
            log.warning("forced alignment skipped: %s", exc)

    # 6. Render variants (one heavy job per moment, best first)
    _set(vid, status="rendering", progress=0,
         stage_message=f"{len(moments)} moments found · waiting for the renderer")
    for rank, m in enumerate(sorted(moments, key=lambda m: -m.final_score)):
        enqueue("render_moment", {"moment_id": m.id}, priority=-rank, dedupe_key=f"render:{m.id}")
    ctx.progress(1.0, f"{len(moments)} moments queued for rendering", force=True)


def _maybe_progress(video_id: int, frac: float, msg: str) -> None:
    try:
        with session() as s:
            v = s.get(Video, video_id)
            if v:
                v.progress, v.stage_message = round(frac, 3), msg[:200]
                s.add(v)
                s.commit()
    except Exception:
        log.debug("progress update for video %s skipped", video_id, exc_info=True)


# ---------------------------------------------------------------- rendering

def _clip_path(clip: Clip) -> Path:
    d = paths().clips / str(clip.video_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"clip_{clip.id}_{clip.variant}.mp4"


def render_one(clip: Clip, moment: Moment, video: Video, src: Source, spec, ctx: JobContext | None = None,
               lo: float = 0, hi: float = 1) -> Clip:
    from . import quality
    from .analytics.predictor import predict_clip
    from .render.compositor import render

    out = _clip_path(clip)
    base = out.with_name(out.stem + "_base.mp4")

    def prog(f: float, msg: str = "") -> None:
        if ctx:
            ctx.progress(lo + (hi - lo) * f, f"variant {clip.variant}: {msg}")
            _maybe_progress(video.id, lo + (hi - lo) * f, f"clip “{moment.title[:40]}” variant {clip.variant}: {msg}")

    m = moment
    trim = (clip.meta or {}).get("trim")
    if trim:
        m = Moment.model_validate(moment.model_dump())
        m.start_s = max(0.0, moment.start_s + float(trim.get("start", 0)))
        m.end_s = moment.end_s + float(trim.get("end", 0))
        m.tight_start_s = m.tight_end_s = None
        spec.length = "full"
    try:
        stats = render(video, src, m, spec, base, caption_edits=clip.caption_edits, progress=prog, burn=False)
    except Exception as exc:
        clip.status, clip.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        return save(clip)
    words = json.loads(Path(video.transcript_path).read_text())["words"]
    passed, results, fits = quality.run(base, stats, m, src, words)
    # The final (text burned in) file is made on approval; until then the dashboard previews base + live text.
    out.unlink(missing_ok=True)
    clip.file_path, clip.thumb_path = "", stats.get("thumb", "")
    meta = dict(clip.meta or {})
    meta["base_path"] = str(base)
    meta.pop("burned_hash", None)
    clip.meta = meta
    clip.duration_s, clip.render_stats = stats["duration"], {**stats, "fits": fits}
    clip.edl = stats["edl"]
    clip.qc, clip.qc_passed = results, passed
    clip.status = "review" if passed else "qc_failed"
    clip.error = "" if passed else "; ".join(r["name"] for r in results if r["blocking"] and not r["passed"])
    clip.layout = max(stats["layouts"], key=stats["layouts"].get) if stats.get("layouts") else ""
    clip.music = stats.get("music", "")
    clip.predicted_score, clip.predicted_views = predict_clip(clip, moment)
    return save(clip)


@task("render_moment", lane="heavy", max_attempts=2)
def render_moment(ctx: JobContext, p: dict) -> None:
    from .variants import plan_variants

    with session() as s:
        moment = s.get(Moment, p["moment_id"])
        video = s.get(Video, moment.video_id) if moment else None
        src = s.get(Source, video.source_id) if video else None
    if moment is None or video is None:
        return
    rights.require(src)
    specs = plan_variants(moment)
    letters = "ABCDEFGH"
    with session() as s:
        existing = s.exec(select(Clip).where(Clip.moment_id == moment.id)).all()
    done_variants = {c.variant for c in existing if c.status not in ("failed", "rendering")}
    for k, spec in enumerate(specs):
        letter = letters[k]
        if letter in done_variants:
            continue
        clip = next((c for c in existing if c.variant == letter), None) or Clip(
            moment_id=moment.id, video_id=video.id, source_id=src.id, variant=letter)
        clip.hook_text, clip.hook_style = spec.hook_text, spec.hook_style
        clip.caption_style, clip.length_bucket, clip.opening = spec.caption_style, spec.length, spec.opening
        clip.edit_preset = spec.preset
        clip.start_s, clip.end_s = moment.start_s, moment.end_s
        clip.meta = {**(moment.meta or {}), "title": moment.title}
        clip.status = "rendering"
        clip = save(clip)
        render_one(clip, moment, video, src, spec, ctx, k / len(specs), (k + 1) / len(specs))
    moment.status = "rendered"
    save(moment)
    _finish_video_if_done(video.id)


def _finish_video_if_done(video_id: int) -> None:
    with session() as s:
        moment_ids = [m.id for m in s.exec(select(Moment).where(Moment.video_id == video_id)).all()]
        pending = s.exec(select(Job).where(Job.kind == "render_moment", Job.status.in_(("queued", "running")))).all()
        still = [j for j in pending if j.payload.get("moment_id") in moment_ids]
        # The current job is still marked running while we're inside it.
        if len(still) > 1:
            return
        clips = s.exec(select(Clip).where(Clip.video_id == video_id)).all()
        video = s.get(Video, video_id)
    passed = sum(1 for c in clips if c.status == "review")
    _set(video_id, status="ready", progress=1.0, stage_message=f"{len(clips)} clips, {passed} passed checks")
    with session() as s:
        src = s.get(Source, video.source_id)
    if src and src.auto_publish and not settings.get("automation_paused"):
        _auto_publish(src, clips)
    events.emit("clips_ready", f"{passed} clips ready for review",
                f"“{video.title[:70]}”: {len(clips)} generated, {passed} passed quality checks",
                level="success" if passed else "warning", video_id=video_id, generated=len(clips), passed=passed)


def _auto_publish(src: Source, clips: list[Clip]) -> None:
    """Best passing variant of each moment, top N by predicted score, scheduled onto the source's pages."""
    from .publish.schedule import auto_targets, schedule_clip

    best: dict[int, Clip] = {}
    for c in clips:
        if c.status != "review":
            continue
        if c.moment_id not in best or c.predicted_score > best[c.moment_id].predicted_score:
            best[c.moment_id] = c
    chosen = sorted(best.values(), key=lambda c: -c.predicted_score)[: max(1, src.auto_publish_top)]
    n = 0
    for c in chosen:
        c.status, c.reviewed_at = "approved", utcnow()
        save(c)
        targets = src.target_accounts or auto_targets(c)
        n += len(schedule_clip(c, targets))
    if n:
        events.emit("scheduled", f"Auto-scheduled {n} posts from {src.name}", "Hands-off mode is on for this source.",
                    level="success", source_id=src.id)


def overlay_hash(clip: Clip) -> str:
    import hashlib

    ov = (clip.meta or {}).get("overlay") or {}
    return hashlib.md5(json.dumps([ov, clip.render_stats.get("overlay")], sort_keys=True, default=str).encode()).hexdigest()


def needs_burn(clip: Clip) -> bool:
    meta = clip.meta or {}
    if not meta.get("base_path"):
        return False                       # legacy clip rendered with text already burned in
    return not clip.file_path or not Path(clip.file_path).exists() or meta.get("burned_hash") != overlay_hash(clip)


@task("burn_clip", lane="light", max_attempts=2)
def burn_clip(ctx: JobContext, p: dict) -> None:
    """Bake the (possibly edited) text layer onto the base clip. Seconds, not a re-render."""
    from .render.burn import burn

    with session() as s:
        clip = s.get(Clip, p["clip_id"])
    if clip is None or not needs_burn(clip):
        return
    meta = dict(clip.meta or {})
    base = Path(meta["base_path"])
    out = _clip_path(clip)
    tmp = out.with_name(out.stem + "_tmp.mp4")
    burn(base, clip.render_stats["overlay"], clip.render_stats.get("words", []), meta.get("overlay"), tmp,
         progress=lambda f, m: ctx.progress(f, m))
    tmp.replace(out)
    with session() as s:
        fresh = s.get(Clip, clip.id)
        fresh.file_path = str(out)
        mm = dict(fresh.meta or {})
        mm["burned_hash"] = overlay_hash(fresh)
        fresh.meta = mm
        s.add(fresh)
        s.commit()


@task("rerender_clip", lane="heavy", max_attempts=1)
def rerender_clip(ctx: JobContext, p: dict) -> None:
    from .render.compositor import RenderSpec

    with session() as s:
        clip = s.get(Clip, p["clip_id"])
        moment = s.get(Moment, clip.moment_id)
        video = s.get(Video, clip.video_id)
        src = s.get(Source, clip.source_id)
    rights.require(src)
    spec = RenderSpec(hook_text=clip.hook_text, hook_style=clip.hook_style, caption_style=clip.caption_style,
                      preset=clip.edit_preset or "clean", length=clip.length_bucket, opening=clip.opening,
                      layout=(clip.meta or {}).get("layout_override", "auto"))
    clip.status = "rendering"
    save(clip)
    render_one(clip, moment, video, src, spec, ctx)
    # Once the video's last re-render finishes, mark it ready again (no notifications or auto-posting:
    # those already happened when it was first made).
    with session() as s:
        clips = s.exec(select(Clip).where(Clip.video_id == video.id)).all()
    if not any(c.status == "rendering" for c in clips):
        passed = sum(1 for c in clips if c.status == "review")
        _set(video.id, status="ready", progress=1.0, stage_message=f"{len(clips)} clips, {passed} passed checks")
