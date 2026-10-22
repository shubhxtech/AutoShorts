"""Viral detection: which videos are outperforming their channel right now.

A video is "viral" relative to its own channel, not in absolute terms. We
compare its views with the channel's median and its views-per-day with the
channel's median velocity, then combine the two.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from statistics import median

from ..models import utcnow


@dataclass
class Candidate:
    external_id: str
    url: str
    title: str
    views: int
    published_at: datetime | None
    duration_s: float
    channel: str = ""
    channel_id: str = ""
    thumbnail: str = ""
    viral_score: float = 0.0
    velocity: float = 0.0


def age_days(published_at: datetime | None) -> float | None:
    if published_at is None:
        return None
    return max(0.25, (utcnow() - published_at).total_seconds() / 86400)


def score(cands: list[Candidate]) -> list[Candidate]:
    """Fill in viral_score and velocity for each candidate, relative to the batch."""
    views = [c.views for c in cands if c.views > 0]
    if not views:
        return cands
    med_views = max(1.0, median(views))
    vels = []
    for c in cands:
        age = age_days(c.published_at)
        c.velocity = c.views / age if age else 0.0
        if c.velocity:
            vels.append(c.velocity)
    med_vel = max(1.0, median(vels)) if vels else 0.0
    for c in cands:
        outlier = c.views / med_views
        if med_vel and c.velocity:
            vel_ratio = c.velocity / med_vel
            c.viral_score = math.sqrt(max(outlier, 1e-3) * max(vel_ratio, 1e-3))
        else:
            c.viral_score = outlier
        c.viral_score = round(c.viral_score, 2)
    return cands


def absolute_score(views: int, published_at: datetime | None, subscribers: int | None = None) -> float:
    """For search results with no channel baseline: log-velocity, optionally relative to channel size."""
    age = age_days(published_at) or 30
    vel = views / age
    s = math.log10(max(vel, 1))
    if subscribers:
        s += math.log10(max(views, 1) / max(subscribers, 1) + 1)
    return round(s, 2)
