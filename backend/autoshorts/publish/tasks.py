"""Publishing jobs: upload due posts, keep tokens fresh."""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import select

from .. import logs, rights
from ..db import save, session
from ..jobs.queue import JobContext, Retry, task
from ..models import Account, Clip, Post, Source, utcnow
from ..notify import events
from . import instagram, youtube
from .schedule import daily_limit_ok

log = logs.get("publish")
MAX_ATTEMPTS = 4


@task("publish_post", lane="light", max_attempts=1)
def publish_post(ctx: JobContext, p: dict) -> None:
    with session() as s:
        post = s.get(Post, p["post_id"])
        if post is None or post.status not in ("scheduled", "failed"):
            return
        clip = s.get(Clip, post.clip_id)
        acc = s.get(Account, post.account_id)
        src = s.get(Source, clip.source_id) if clip else None
    if clip is None or acc is None:
        return

    def fail(msg: str, retry_in: float | None = None) -> None:
        post.attempts += 1
        post.error = msg[:1000]
        if retry_in is not None and post.attempts < MAX_ATTEMPTS:
            post.status = "scheduled"
            post.scheduled_at = utcnow() + timedelta(seconds=retry_in)
            save(post)
            return
        post.status = "failed"
        save(post)
        events.emit("post_failed", f"Post failed on {acc.name}", msg[:300], level="error", post_id=post.id)

    chk = rights.check_source(src, acc.platform)
    if not chk.ok:
        fail("rights check failed: " + "; ".join(chk.problems))
        return
    if acc.status == "expired":
        fail(f"{acc.name} needs to be reconnected (token expired)")
        return
    if not daily_limit_ok(acc):
        fail("daily publishing limit reached", retry_in=3 * 3600)
        return

    from ..pipeline import needs_burn

    if needs_burn(clip):
        # Text edits since approval: bake them in first (fast), then post.
        from ..jobs.queue import enqueue

        enqueue("burn_clip", {"clip_id": clip.id}, priority=9, dedupe_key=f"burn:{clip.id}")
        raise Retry(30, "adding text to the video first")

    post.status = "publishing"
    save(post)
    try:
        if acc.platform == "youtube":
            res = youtube.upload(acc, clip.file_path, post.title, post.description, post.tags,
                                 language=acc.language or "en", progress=lambda f, m: ctx.progress(f, m))
        else:
            res = instagram.publish_reel(acc, clip.id, clip.file_path, post.caption,
                                         progress=lambda f, m: ctx.progress(f, m))
    except youtube.QuotaExceeded as exc:
        fail(str(exc), retry_in=youtube.seconds_until_quota_reset())
        return
    except instagram.IGError as exc:
        if exc.code == 190:
            acc.status, acc.status_detail = "expired", str(exc)[:300]
            save(acc)
            events.emit("token_expiring", f"{acc.name}: Instagram token expired", "Reconnect the page in Pages.",
                        level="error", account_id=acc.id)
        fail(f"Instagram: {exc}", retry_in=600 if exc.retriable else None)
        return
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "invalid_grant" in msg or "Token has been expired" in msg:
            youtube.mark_expired(acc, msg)
            events.emit("token_expiring", f"{acc.name}: YouTube access revoked", "Reconnect the channel.",
                        level="error", account_id=acc.id)
            fail(msg)
            return
        fail(msg, retry_in=900)
        return

    post.status, post.error = "published", ""
    post.external_id, post.url = res["id"], res.get("url", "")
    post.container_id = res.get("container", "")
    post.published_at = utcnow()
    save(post)
    with session() as s:
        remaining = s.exec(select(Post).where(Post.clip_id == clip.id, Post.status.in_(("scheduled", "publishing")))).all()
    if not remaining:
        clip.status = "published"
        save(clip)
    note = ""
    if acc.platform == "youtube" and res.get("privacy") == "private" and acc.privacy_status == "public":
        note = " (uploaded as private: your Google API project still needs its compliance audit)"
    events.emit("published", f"Posted to {acc.name}", (post.title or post.caption[:80]) + note,
                level="success", post_id=post.id, url=post.url)


@task("refresh_tokens", lane="light")
def refresh_tokens(ctx: JobContext, p: dict) -> None:
    with session() as s:
        accounts = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    for acc in accounts:
        if acc.platform == "instagram":
            if acc.token_expires_at and acc.token_expires_at - utcnow() < timedelta(days=20):
                if not instagram.refresh_token(acc):
                    events.emit("token_expiring", f"{acc.name}: Instagram token couldn't be refreshed",
                                "Reconnect the page before it expires.", level="warning", account_id=acc.id)
        elif acc.platform == "youtube":
            try:
                youtube._creds(acc)
            except Exception as exc:  # noqa: BLE001
                youtube.mark_expired(acc, str(exc))
                events.emit("token_expiring", f"{acc.name}: YouTube needs reconnecting", str(exc)[:200],
                            level="warning", account_id=acc.id)
    # Rights that run out soon.
    with session() as s:
        soon = s.exec(select(Source).where(Source.active == True, Source.expires_at != None)).all()  # noqa: E711,E712
    for src in soon:
        days = (src.expires_at - utcnow()).days
        if 0 <= days <= 7:
            events.emit("rights_expiring", f"Rights for {src.name} expire in {days} days",
                        "Renew the agreement or the pipeline will stop using this source.", level="warning",
                        source_id=src.id)
