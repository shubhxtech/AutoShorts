"""A small durable job queue on top of SQLite.

Two lanes: "heavy" jobs (download, transcribe, render) run one at a time so the
Mac stays responsive; "light" jobs (publishing, metrics, comments) run in parallel.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import select

from .. import logs
from ..db import engine, session
from ..models import Job, utcnow

log = logs.get("jobs")


class Canceled(BaseException):
    """Raised inside a job when the user cancels it. BaseException so generic handlers can't swallow it."""


class Retry(Exception):
    """Raise from a task to retry later without counting it as an error."""

    def __init__(self, delay_s: float = 60, message: str = ""):
        super().__init__(message)
        self.delay_s = delay_s


@dataclass
class TaskSpec:
    fn: Callable[[JobContext, dict], Any]
    lane: str
    max_attempts: int


REGISTRY: dict[str, TaskSpec] = {}


def task(kind: str, lane: str = "light", max_attempts: int = 3):
    def deco(fn):
        REGISTRY[kind] = TaskSpec(fn, lane, max_attempts)
        return fn
    return deco


def enqueue(kind: str, payload: dict | None = None, *, priority: int = 0, delay_s: float = 0,
            dedupe_key: str = "", lane: str | None = None) -> Job | None:
    """Queue a job. With `dedupe_key`, skips if an identical job is already waiting or running."""
    spec = REGISTRY.get(kind)
    if spec is None:
        from . import tasks  # noqa: F401 - the API process registers tasks lazily

        spec = REGISTRY.get(kind)
    with session() as s:
        if dedupe_key:
            existing = s.exec(select(Job).where(Job.dedupe_key == dedupe_key,
                                                Job.status.in_(("queued", "running")))).first()
            if existing:
                return None
        job = Job(kind=kind, payload=payload or {}, priority=priority, dedupe_key=dedupe_key,
                  lane=lane or (spec.lane if spec else "light"),
                  max_attempts=spec.max_attempts if spec else 3,
                  run_after=utcnow() + timedelta(seconds=delay_s))
        s.add(job)
        s.commit()
        s.refresh(job)
        return job


def cancel(job_id: int) -> bool:
    with session() as s:
        job = s.get(Job, job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.status = "canceled"
        job.finished_at = utcnow()
        s.add(job)
        s.commit()
        return True


def _sql_now() -> str:
    # Same text format SQLAlchemy uses for DateTime columns on SQLite (UTC, no offset).
    return utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")


def claim(lane: str) -> Job | None:
    now = _sql_now()
    with engine().begin() as conn:
        row = conn.execute(text(
            "SELECT id FROM job WHERE status='queued' AND lane=:lane AND run_after<=:now "
            "ORDER BY priority DESC, id ASC LIMIT 1"), {"lane": lane, "now": now}).first()
        if not row:
            return None
        updated = conn.execute(text(
            "UPDATE job SET status='running', started_at=:now, attempts=attempts+1 "
            "WHERE id=:id AND status='queued'"), {"id": row[0], "now": now}).rowcount
        if updated != 1:
            return None
    with session() as s:
        return s.get(Job, row[0])


def recover_stale() -> int:
    """Jobs left 'running' by a crashed worker go back to the queue."""
    with engine().begin() as conn:
        return conn.execute(text("UPDATE job SET status='queued' WHERE status='running'")).rowcount


class JobContext:
    def __init__(self, job: Job):
        self.job = job
        self._last_write = 0.0

    @property
    def id(self) -> int:
        return self.job.id  # type: ignore[return-value]

    def progress(self, fraction: float, message: str = "", force: bool = False) -> None:
        import time

        now = time.monotonic()
        if not force and now - self._last_write < 1.0:
            return
        self._last_write = now
        with session() as s:
            job = s.get(Job, self.job.id)
            if job is None:
                return
            if job.status == "canceled":
                raise Canceled()
            job.progress = max(0.0, min(1.0, fraction))
            if message:
                job.message = message[:500]
            s.add(job)
            s.commit()

    def check_canceled(self) -> None:
        with session() as s:
            job = s.get(Job, self.job.id)
            if job is not None and job.status == "canceled":
                raise Canceled()


def run_one(job: Job) -> None:
    spec = REGISTRY.get(job.kind)
    ctx = JobContext(job)
    status, error, run_after, retried = "done", "", None, False
    try:
        if spec is None:
            raise RuntimeError(f"unknown job kind {job.kind!r}")
        log.info("job %s %s start %s", job.id, job.kind, job.payload)
        spec.fn(ctx, job.payload or {})
    except Canceled:
        status = "canceled"
    except Retry as r:
        status, error, retried = "queued", str(r), True
        run_after = utcnow() + timedelta(seconds=r.delay_s)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        log.error("job %s %s failed: %s\n%s", job.id, job.kind, error, traceback.format_exc())
        if job.attempts < job.max_attempts:
            status = "queued"
            run_after = utcnow() + timedelta(minutes=2 ** job.attempts)
        else:
            status = "failed"
    with session() as s:
        fresh = s.get(Job, job.id)
        if fresh is None:
            return
        if fresh.status == "canceled":
            status = "canceled"
        fresh.status = status
        fresh.error = error[:4000]
        if retried:
            fresh.attempts = max(0, fresh.attempts - 1)
        if status == "done":
            fresh.progress = 1.0
        if run_after is not None and status == "queued":
            fresh.run_after = run_after
        if status in ("done", "failed", "canceled"):
            fresh.finished_at = utcnow()
        s.add(fresh)
        s.commit()
    log.info("job %s %s -> %s", job.id, job.kind, status)
