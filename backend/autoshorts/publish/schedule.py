"""Scheduling approved clips onto pages.

Rules:
  - each page posts at its own time slots (local time), up to its daily cadence, with a minimum gap
  - with learn_slots on, the bandit ranks slot hours by how well they've done for that page
  - a page only gets clips whose rights cover its platform and whose length fits the platform
  - the same variant never goes to two pages: other pages get a sibling variant of the same moment
    when one is available, otherwise the post is pushed at least 48 hours apart
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import select

from .. import rights, settings
from ..analytics import bandit
from ..db import save, session
from ..models import Account, Clip, Moment, Post, Source, utcnow


def build_metadata(clip: Clip, acc: Account, src: Source) -> dict:
    meta = clip.meta or {}
    credit = ""
    if src.credit_text:
        credit = f"🎙 Full episode: {src.credit_text}" + (f" {src.credit_link}" if src.credit_link else "")
    tags = [t for t in meta.get("tags", []) if t][:15]
    hashtags = [h for h in meta.get("hashtags", []) if h]
    extra = [h.strip().lstrip("#") for h in (acc.default_hashtags or "").replace(",", " ").split() if h.strip()]
    if acc.platform == "youtube":
        title = (meta.get("youtube_title") or clip.hook_text or meta.get("title") or "").strip()
        if len(title) > 92:
            title = title[:91].rstrip() + "…"
        tags_line = " ".join(f"#{h}" for h in (["shorts"] + hashtags[:3] + extra[:2]))
        desc = "\n\n".join(x for x in [meta.get("youtube_description", ""), credit, tags_line] if x)
        return {"title": title, "description": desc, "tags": tags, "caption": ""}
    lines = [meta.get("instagram_caption") or clip.hook_text]
    if getattr(src, "cta_keyword", ""):
        lines.append(f"💬 Comment \"{src.cta_keyword.upper()}\" and I'll DM you the link")
    if credit:
        lines.append(credit.replace(src.credit_link, "(link in bio)") if src.credit_link else credit)
    all_tags = list(dict.fromkeys(hashtags + extra))[:20]
    lines.append(" ".join(f"#{h}" for h in all_tags))
    return {"title": "", "description": "", "tags": [], "caption": "\n\n".join(l for l in lines if l)}


def _local_slots(acc: Account, day: datetime) -> list[datetime]:
    tz = ZoneInfo(acc.timezone or "UTC")
    out = []
    for s in acc.time_slots or ["09:00", "13:00", "19:30"]:
        try:
            hh, mm = (int(x) for x in s.split(":"))
        except ValueError:
            continue
        local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        out.append(local.astimezone(timezone.utc))
    return out


def _taken(acc_id: int) -> list[datetime]:
    with session() as s:
        posts = s.exec(select(Post).where(Post.account_id == acc_id,
                                          Post.status.in_(("scheduled", "publishing", "published")),
                                          Post.scheduled_at >= utcnow() - timedelta(days=2))).all()
    return [p.scheduled_at for p in posts]


def next_slot(acc: Account, not_before: datetime | None = None) -> datetime:
    now = utcnow() + timedelta(minutes=5)
    start = max(now, not_before or now)
    taken = _taken(acc.id)
    gap = timedelta(minutes=max(10, acc.min_gap_minutes or 120))
    tz = ZoneInfo(acc.timezone or "UTC")
    rng = random.Random()
    hours = sorted({(int(s.split(":")[0])) for s in acc.time_slots or []}) or [9, 13, 19]
    for day_off in range(0, 30):
        day = (start.astimezone(tz) + timedelta(days=day_off)).date()
        day_dt = datetime(day.year, day.month, day.day, tzinfo=tz)
        slots = [s for s in _local_slots(acc, day_dt) if s >= start]
        if acc.learn_slots and len(slots) > 1:
            post = bandit.posterior(f"account:{acc.id}", "slot_hour", [str(h) for h in hours])
            score = {h: rng.betavariate(*post[str(h)]) for h in hours}
            slots.sort(key=lambda s: -score.get(s.astimezone(tz).hour, 0.5))
        same_day = [t for t in taken if t.astimezone(tz).date() == day]
        if len(same_day) >= max(1, acc.cadence_per_day):
            continue
        for s in slots:
            if all(abs(s - t) >= gap for t in taken):
                return s
    return start + timedelta(days=30)


def schedule_clip(clip: Clip, account_ids: list[int], at: datetime | None = None) -> list[Post]:
    """Create scheduled posts for an approved clip. Returns the posts created."""
    created: list[Post] = []
    with session() as s:
        src = s.get(Source, clip.source_id)
        moment = s.get(Moment, clip.moment_id)
        siblings = s.exec(select(Clip).where(Clip.moment_id == clip.moment_id, Clip.id != clip.id,
                                             Clip.status.in_(("approved", "review")))).all()
        accounts = [s.get(Account, a) for a in account_ids]
    fits = (clip.render_stats or {}).get("fits", {})
    used_variants: set[int] = set()
    for acc in accounts:
        if acc is None or not acc.active:
            continue
        chk = rights.check_source(src, acc.platform)
        if not chk.ok or fits.get(acc.platform) is False:
            continue
        # Same variant already going out on another page?
        with session() as s:
            elsewhere = s.exec(select(Post).where(Post.clip_id == clip.id, Post.account_id != acc.id,
                                                  Post.status.in_(("scheduled", "publishing", "published")))).all()
            already = s.exec(select(Post).where(Post.clip_id == clip.id, Post.account_id == acc.id,
                                                Post.status.in_(("scheduled", "publishing", "published")))).first()
        if already:
            continue
        use = clip
        not_before = at
        if elsewhere or clip.id in used_variants:
            alt = next((c for c in siblings if c.id not in used_variants
                        and (c.render_stats or {}).get("fits", {}).get(acc.platform, True)), None)
            if alt is not None:
                use = alt
                if alt.status == "review":
                    alt.status = "approved"
                    alt.reviewed_at = utcnow()
                    save(alt)
            else:
                latest = max([p.scheduled_at for p in elsewhere] or [utcnow()])
                not_before = max(not_before or utcnow(), latest + timedelta(hours=48))
        used_variants.add(use.id)
        when = at if (at and use is clip and not elsewhere) else next_slot(acc, not_before)
        md = build_metadata(use, acc, src)
        post = save(Post(clip_id=use.id, account_id=acc.id, platform=acc.platform, scheduled_at=when,
                         title=md["title"], description=md["description"], caption=md["caption"], tags=md["tags"],
                         slot_hour=when.astimezone(ZoneInfo(acc.timezone or "UTC")).hour))
        created.append(post)
        use.status = "scheduled"
        save(use)
    if moment is not None and created:
        moment.status = "rendered"
        save(moment)
    return created


def auto_targets(clip: Clip) -> list[int]:
    """Pages to use when the reviewer didn't pick any: every active page whose niche matches the source."""
    with session() as s:
        src = s.get(Source, clip.source_id)
        accs = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    niche = (src.niche or "").lower()
    matching = [a.id for a in accs if not niche or not a.niche or a.niche.lower() == niche]
    return matching


def daily_limit_ok(acc: Account) -> bool:
    with session() as s:
        n = len(s.exec(select(Post).where(Post.account_id == acc.id, Post.status == "published",
                                          Post.published_at >= utcnow() - timedelta(hours=24))).all())
    if acc.platform == "instagram":
        return n < int(settings.get("instagram_daily_limit"))
    return True
