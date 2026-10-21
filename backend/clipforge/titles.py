"""Ten title options for a clip, written by the local LLM in different proven angles."""

from __future__ import annotations

from pydantic import BaseModel, Field

from . import llm, logs
from .db import save, session
from .jobs.queue import JobContext, task
from .models import Clip, Moment, Video

log = logs.get("titles")


class TitleOptions(BaseModel):
    titles: list[str] = Field(description="10 different titles, max 60 characters each")


ANGLES = ("curiosity gap, a bold claim, a number or stat, a question, a contrarian take, a 'how to', a story tease, "
          "a mistake to avoid, a direct promise to the viewer, and a short punchy one (max 5 words)")


@task("suggest_titles", lane="light", max_attempts=1)
def suggest_titles(ctx: JobContext, p: dict) -> None:
    with session() as s:
        clip = s.get(Clip, p["clip_id"])
        moment = s.get(Moment, clip.moment_id) if clip else None
        video = s.get(Video, clip.video_id) if clip else None
    if clip is None or moment is None:
        return
    lang = (video.language if video else "en") or "en"
    note = ("Write in the clip's language: Hindi in Devanagari or natural Hinglish; common English words may stay."
            if lang == "hi" else "Write in English.")
    res = llm.structured(TitleOptions,
                         "You write titles for YouTube Shorts and Instagram Reels that make people stop scrolling. "
                         "Each title must be true to what is said in the clip (no clickbait the clip doesn't pay off), "
                         "no hashtags, no emoji spam, no ALL CAPS sentences.",
                         f"Clip transcript:\n{moment.text[:3000]}\n\nCurrent title: {(clip.meta or {}).get('youtube_title') or moment.title}\n"
                         f"Write 10 titles, one per angle: {ANGLES}. {note}",
                         max_tokens=900, temperature=0.8)
    seen, titles = set(), []
    for t in res.titles:
        t = t.strip().strip('"').strip()
        if t and t.lower() not in seen and len(t) <= 100:
            seen.add(t.lower())
            titles.append(t)
    with session() as s:
        c = s.get(Clip, clip.id)
        c.meta = {**(c.meta or {}), "title_options": titles[:10]}
        s.add(c)
        s.commit()
    log.info("clip %s: %d title options", clip.id, len(titles))
