"""Numbers for the dashboard header and the Telegram /status command."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlmodel import select

from ..db import session
from ..models import Clip, Comment, Event, Post, Video, utcnow


def _count(s, stmt) -> int:
    return int(s.exec(select(func.count()).select_from(stmt.subquery())).one())


def today_summary() -> dict:
    now = utcnow()
    day = now - timedelta(hours=24)
    with session() as s:
        ingested = _count(s, select(Video.id).where(Video.created_at >= day, Video.status != "discovered"))
        generated = _count(s, select(Clip.id).where(Clip.created_at >= day))
        passed = _count(s, select(Clip.id).where(Clip.created_at >= day, Clip.qc_passed == True))  # noqa: E712
        review = _count(s, select(Clip.id).where(Clip.status == "review"))
        scheduled = _count(s, select(Post.id).where(Post.status == "scheduled"))
        published_today = _count(s, select(Post.id).where(Post.status == "published", Post.published_at >= day))
        failed = _count(s, select(Post.id).where(Post.status == "failed"))
        replies = _count(s, select(Comment.id).where(Comment.reply_status == "drafted"))
        processing = _count(s, select(Video.id).where(Video.status.in_(
            ("queued", "downloading", "transcribing", "analyzing", "mining", "rendering"))))
        recent = s.exec(select(Post).where(Post.published_at >= now - timedelta(days=3))).all()
        unread = _count(s, select(Event.id).where(Event.read == False))  # noqa: E712
    views_24h = sum(p.views for p in recent if p.published_at and p.published_at >= day)
    return {"ingested": ingested, "generated": generated, "passed": passed, "review": review,
            "scheduled": scheduled, "published_today": published_today, "failed_posts": failed,
            "replies_waiting": replies, "processing": processing, "views_24h": views_24h,
            "views_72h": sum(p.views for p in recent), "earnings_72h": round(sum(p.earnings for p in recent), 2),
            "unread_events": unread}
