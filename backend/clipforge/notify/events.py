"""Activity feed. Every event shows on the dashboard; important ones also go to Telegram."""

from __future__ import annotations

from sqlmodel import select

from .. import logs
from ..db import session
from ..models import Event

log = logs.get("events")

# Kinds forwarded to Telegram.
TELEGRAM_KINDS = {"clips_ready", "viral", "post_failed", "token_expiring", "replies_waiting", "weekly_report",
                  "rights_expiring", "published", "worker_error"}


def emit(kind: str, title: str, body: str = "", level: str = "info", **data) -> Event:
    ev = Event(kind=kind, title=title, body=body, level=level, data=data,
               telegram_sent=kind not in TELEGRAM_KINDS)
    with session() as s:
        s.add(ev)
        s.commit()
        s.refresh(ev)
    log.info("[%s] %s %s", kind, title, body[:120])
    return ev


def flush_telegram() -> int:
    from . import telegram

    if not telegram.configured():
        return 0
    with session() as s:
        pending = s.exec(select(Event).where(Event.telegram_sent == False).order_by(Event.id).limit(20)).all()  # noqa: E712
    sent = 0
    for ev in pending:
        icon = {"success": "✅", "warning": "⚠️", "error": "❌"}.get(ev.level, "🔔")
        if ev.kind == "viral":
            icon = "🚀"
        text = f"{icon} <b>{telegram.escape(ev.title)}</b>"
        if ev.body:
            text += f"\n{telegram.escape(ev.body)}"
        ok = telegram.send(text)
        with session() as s:
            fresh = s.get(Event, ev.id)
            if fresh:
                fresh.telegram_sent = True if ok else fresh.telegram_sent
                s.add(fresh)
                s.commit()
        if not ok:
            break
        sent += 1
    return sent
