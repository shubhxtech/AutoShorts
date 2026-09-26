"""Trend scout: find videos going viral in your niches right now.

Results land in the Viral Finder. They are leads, not inputs: nothing is
downloaded or clipped until you attach a rights record (your own channel, a
licence, written creator permission, or a clipping campaign that covers the
creator). Channels already covered by a rights record are flagged and can be
queued straight away.
"""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import select

from .. import logs, settings
from ..db import save, session
from ..jobs.queue import JobContext, task
from ..models import Discovery, Source, utcnow
from . import listing, viral

log = logs.get("scout")


def covering_source(channel_id: str, channel_url: str = "") -> Source | None:
    """A rights record that covers this channel (by channel id or URL)."""
    with session() as s:
        srcs = s.exec(select(Source).where(Source.active == True)).all()  # noqa: E712
    for src in srcs:
        urls = [src.url, *(src.extra_urls or [])]
        if any(channel_id and channel_id in u for u in urls) or any(channel_url and u and u.rstrip("/") in channel_url for u in urls):
            return src
    return None


def run(queries: list[str], max_age_days: int = 14, per_query: int = 25) -> list[Discovery]:
    found: list[Discovery] = []
    cutoff = utcnow() - timedelta(days=max_age_days)
    for q in queries:
        try:
            cands = listing.youtube_search(q, per_query)
        except Exception as exc:  # noqa: BLE001
            log.warning("search %r failed: %s", q, exc)
            continue
        for c in cands:
            if c.duration_s and c.duration_s < 240:
                continue  # skip shorts and trailers; we want long-form to clip from
            if c.published_at and c.published_at < cutoff:
                continue
            score = viral.absolute_score(c.views, c.published_at)
            with session() as s:
                d = s.exec(select(Discovery).where(Discovery.external_id == c.external_id)).first()
            d = d or Discovery(external_id=c.external_id)
            d.url, d.title, d.channel, d.channel_id = c.url, c.title, c.channel, c.channel_id
            d.channel_url = f"https://www.youtube.com/channel/{c.channel_id}" if c.channel_id else ""
            d.thumbnail, d.query, d.published_at = c.thumbnail, q, c.published_at
            d.duration_s, d.view_count, d.viral_score = c.duration_s, c.views, score
            d.velocity = c.views / (viral.age_days(c.published_at) or 30)
            src = covering_source(c.channel_id, d.channel_url)
            if src and d.status == "new":
                d.source_id = src.id
            found.append(save(d))
    return found


@task("trend_scout", lane="light")
def trend_scout(ctx: JobContext, p: dict) -> None:
    queries = p.get("queries") or settings.get("scout_queries") or []
    run(queries, int(settings.get("scout_max_age_days") or 14))
