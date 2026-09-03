"""Periodic automation: watching sources, publishing due posts, metrics, comments, upkeep."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from sqlmodel import select

from .. import logs, settings
from ..db import session
from ..models import Post, Source, utcnow
from .queue import enqueue

log = logs.get("scheduler")


def _last(name: str) -> datetime | None:
    v = settings.get(f"sched:{name}")
    try:
        return datetime.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _due(name: str, every: timedelta) -> bool:
    last = _last(name)
    if last is None or utcnow() - last >= every:
        settings.set(f"sched:{name}", utcnow().isoformat())
        return True
    return False


class Scheduler:
    def __init__(self, stop: threading.Event):
        self.stop = stop
        self.thread = threading.Thread(target=self._run, name="scheduler", daemon=True)

    def start(self) -> None:
        self.thread.start()
        from ..notify import telegram

        telegram.start_polling(self.stop)

    def _run(self) -> None:
        self.stop.wait(3)
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.exception("scheduler tick failed: %s", exc)
            self.stop.wait(20)

    def tick(self) -> None:
        paused = bool(settings.get("automation_paused"))
        now = utcnow()

        if not paused:
            with session() as s:
                sources = s.exec(select(Source).where(Source.active == True, Source.watch == True)).all()  # noqa: E712
            for src in sources:
                interval = timedelta(hours=max(1, src.watch_interval_hours or 6))
                if src.last_checked_at is None or now - src.last_checked_at >= interval:
                    enqueue("watch_source", {"source_id": src.id}, dedupe_key=f"watch:{src.id}")

            with session() as s:
                due = s.exec(select(Post).where(Post.status == "scheduled", Post.scheduled_at <= now)).all()
            for post in due:
                enqueue("publish_post", {"post_id": post.id}, priority=5, dedupe_key=f"publish:{post.id}")

        metrics_every = timedelta(minutes=int(settings.get("metrics_interval_minutes") or 60))
        if _due("metrics", metrics_every):
            enqueue("pull_metrics", {}, dedupe_key="pull_metrics")
        comments_every = timedelta(minutes=int(settings.get("comments_interval_minutes") or 30))
        if _due("comments", comments_every):
            enqueue("fetch_comments", {}, dedupe_key="fetch_comments")
        if _due("learn", timedelta(hours=3)):
            enqueue("learn", {}, dedupe_key="learn")
        if _due("tokens", timedelta(hours=12)):
            enqueue("refresh_tokens", {}, dedupe_key="refresh_tokens")
        if _due("cleanup", timedelta(hours=24)):
            enqueue("cleanup", {}, dedupe_key="cleanup")
        if _due("weekly_report", timedelta(days=7)):
            enqueue("weekly_report", {}, dedupe_key="weekly_report", delay_s=60)
        if settings.get("scout_queries") and _due("scout", timedelta(hours=12)):
            enqueue("trend_scout", {}, dedupe_key="trend_scout")

        from ..notify import events

        events.flush_telegram()
