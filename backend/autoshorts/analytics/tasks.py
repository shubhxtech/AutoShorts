"""Metrics pull, learning loop and the weekly optimizer report."""

from __future__ import annotations

from datetime import timedelta
from statistics import median

import numpy as np
from sqlmodel import select

from .. import logs, settings
from ..db import save, session
from ..jobs.queue import JobContext, task
from ..models import Account, Clip, MetricSnapshot, Post, Source, utcnow
from ..notify import events
from . import bandit, predictor

log = logs.get("analytics")


def _interp_views(snaps: list[MetricSnapshot], hours: float) -> int | None:
    pts = sorted((s.hours, s.views) for s in snaps)
    if not pts or pts[-1][0] < hours:
        return None
    xs, ys = zip(*([(0.0, 0)] + pts))
    return int(np.interp(hours, xs, ys))


@task("pull_metrics", lane="light")
def pull_metrics(ctx: JobContext, p: dict) -> None:
    from ..publish import instagram, youtube

    now = utcnow()
    with session() as s:
        accounts = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    thresholds = sorted(int(x) for x in (settings.get("viral_thresholds") or []))
    for acc in accounts:
        with session() as s:
            posts = s.exec(select(Post).where(Post.account_id == acc.id, Post.status == "published",
                                              Post.published_at >= now - timedelta(days=30))).all()
        if not posts:
            continue
        try:
            if acc.platform == "youtube":
                stats = youtube.video_stats(acc, [x.external_id for x in posts])
                deep = youtube.analytics(acc, [x.external_id for x in posts], min(x.published_at for x in posts))
            else:
                stats, deep = {}, {}
                for x in posts:
                    ins = instagram.insights(acc, x.external_id)
                    stats[x.external_id] = {"views": int(ins.get("views", 0)), "likes": int(ins.get("likes", 0)),
                                            "comments": int(ins.get("comments", 0)), "shares": int(ins.get("shares", 0)),
                                            "saves": int(ins.get("saved", 0)), "reach": int(ins.get("reach", 0)),
                                            "followers_gained": int(ins.get("follows", 0)),
                                            "avg_view_duration": float(ins.get("ig_reels_avg_watch_time", 0)) / 1000}
        except Exception as exc:  # noqa: BLE001
            log.warning("metrics for %s failed: %s", acc.name, exc)
            continue
        for x in posts:
            st = {**stats.get(x.external_id, {}), **deep.get(x.external_id, {})}
            if not st:
                continue
            for k, v in st.items():
                if hasattr(x, k) and v is not None:
                    setattr(x, k, v)
            hours = (now - x.published_at).total_seconds() / 3600
            with session() as s:
                s.add(MetricSnapshot(post_id=x.id, hours=hours, views=x.views, likes=x.likes, comments=x.comments,
                                     shares=x.shares, saves=x.saves, reach=x.reach,
                                     avg_view_duration=x.avg_view_duration, avg_view_pct=x.avg_view_pct,
                                     followers_gained=x.followers_gained))
                s.commit()
                snaps = s.exec(select(MetricSnapshot).where(MetricSnapshot.post_id == x.id)).all()
            if x.views_24h is None:
                x.views_24h = _interp_views(snaps, 24)
            if x.views_72h is None:
                x.views_72h = _interp_views(snaps, 72)
            # Estimated campaign earnings (unless entered by hand).
            with session() as s:
                clip = s.get(Clip, x.clip_id)
                src = s.get(Source, clip.source_id) if clip else None
            if src and src.payout_per_1k and not x.earnings_manual:
                x.earnings = round(x.views / 1000 * src.payout_per_1k, 2)
            if acc.platform == "youtube" and hours > 48 and not x.retention:
                x.retention = youtube.retention(acc, x.external_id, x.published_at)
            x.last_metrics_at = now
            for th in thresholds:
                if x.views >= th > x.alerted_views:
                    x.alerted_views = th
                    events.emit("viral", f"Clip #{x.clip_id} crossed {th:,} views",
                                f"{acc.name} · {(x.title or x.caption)[:80]}", level="success", post_id=x.id, url=x.url)
            save(x)


@task("learn", lane="light")
def learn(ctx: JobContext, p: dict) -> None:
    """Feed matured posts (72h+) into the bandit and retrain the predictor."""
    with session() as s:
        matured = s.exec(select(Post).where(Post.status == "published", Post.learned == False,  # noqa: E712
                                            Post.views_72h != None)).all()  # noqa: E711
        all_72 = s.exec(select(Post).where(Post.views_72h != None)).all()  # noqa: E711
    med_by_acc: dict[int, float] = {}
    for acc_id in {x.account_id for x in all_72}:
        vals = [x.views_72h for x in all_72 if x.account_id == acc_id and x.views_72h is not None]
        med_by_acc[acc_id] = median(vals) if vals else 0
    for x in matured:
        with session() as s:
            clip = s.get(Clip, x.clip_id)
        if clip is None:
            continue
        success = (x.views_72h or 0) > med_by_acc.get(x.account_id, 0)
        scope = f"account:{x.account_id}"
        for dim, val in (("hook_style", clip.hook_style), ("caption_style", clip.caption_style),
                         ("length_bucket", clip.length_bucket), ("opening", clip.opening),
                         ("edit_preset", clip.edit_preset)):
            if val:
                bandit.update(dim, val, success, scope=scope)
        if x.slot_hour >= 0:
            bandit.update("slot_hour", str(x.slot_hour), success, scope=scope)
        x.learned = True
        save(x)
    res = predictor.train()
    settings.set("last_learn", {"at": utcnow().isoformat(), "matured": len(matured), **res})


@task("weekly_report", lane="light")
def weekly_report(ctx: JobContext, p: dict) -> None:
    report = build_report(days=7)
    settings.set("weekly_report", report)
    lines = [f"Clips published: {report['published']}",
             f"Total views: {report['views']:,} (median 72h: {report['median_72h']:,})"]
    for dim, best in report["best"].items():
        lines.append(f"Best {dim.replace('_', ' ')}: {best['arm']} ({best['win_rate'] * 100:.0f}% beat median, n={best['pulls']})")
    if report.get("best_hours"):
        lines.append("Best posting hours: " + ", ".join(f"{h}:00" for h in report["best_hours"][:3]))
    events.emit("weekly_report", "Weekly optimizer report", "\n".join(lines), level="info")


def build_report(days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    with session() as s:
        posts = s.exec(select(Post).where(Post.status == "published", Post.published_at >= since)).all()
        clips = {c.id: c for c in s.exec(select(Clip).where(Clip.id.in_([x.clip_id for x in posts] or [0]))).all()}
    v72 = [x.views_72h for x in posts if x.views_72h is not None]
    by_dim: dict[str, dict[str, list[int]]] = {}
    for x in posts:
        c = clips.get(x.clip_id)
        if not c:
            continue
        for dim, val in (("hook_style", c.hook_style), ("caption_style", c.caption_style),
                         ("length_bucket", c.length_bucket), ("opening", c.opening)):
            by_dim.setdefault(dim, {}).setdefault(val, []).append(x.views)
    table = bandit.table("global")
    best = {dim: rows[0] for dim, rows in table.items() if rows and dim in bandit.DIMENSIONS}
    hours = table.get("slot_hour", [])
    top = sorted(posts, key=lambda x: -x.views)[:5]
    losers = sorted([x for x in posts if x.views_72h is not None], key=lambda x: x.views_72h)[:5]
    return {
        "since": since.isoformat(), "published": len(posts), "views": int(sum(x.views for x in posts)),
        "median_72h": int(median(v72)) if v72 else 0,
        "earnings": round(sum(x.earnings for x in posts), 2),
        "by_dimension": {d: {k: {"posts": len(v), "avg_views": int(sum(v) / len(v))} for k, v in vals.items()}
                         for d, vals in by_dim.items()},
        "best": best,
        "best_hours": [int(h["arm"]) for h in hours if h["pulls"] > 0][:5],
        "top": [{"post_id": x.id, "clip_id": x.clip_id, "views": x.views, "url": x.url} for x in top],
        "losers": [{"post_id": x.id, "clip_id": x.clip_id, "views_72h": x.views_72h, "url": x.url} for x in losers],
        "model": {k: v for k, v in (settings.get("predictor") or {}).items() if k in ("samples", "r2", "top_features")},
        "score_weights": settings.get("score_weights") or {},
    }
