"""Database tables.

Everything that flows through the pipeline is traceable back to a `Source`,
which is the rights record. A `Video` cannot exist without one.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """All timestamps are timezone-aware UTC."""
    return datetime.now(UTC)


def JSONField(default: Any = None, **kw):
    factory = lambda: copy.deepcopy(default)  # noqa: E731
    return Field(default_factory=factory, sa_column=Column(JSON), **kw)


# ---------------------------------------------------------------- rights registry

RIGHTS_TYPES = ("own", "licensed", "creator_permission", "clipping_campaign")
SOURCE_KINDS = ("youtube_channel", "youtube_playlist", "youtube_video", "rss", "drive", "local_folder",
                "direct_url", "campaign")


class Source(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kind: str = "youtube_channel"
    url: str = ""
    # Extra URLs: creator channels covered by a clipping campaign, or campaign asset links.
    extra_urls: list = JSONField([])

    rights_type: str = "own"
    proof_kind: str = "email"            # email | contract | campaign_link | other
    proof_url: str = ""
    proof_file: str = ""                 # stored under paths().proofs
    proof_notes: str = ""
    allowed_platforms: list = JSONField(["youtube", "instagram"])
    credit_text: str = ""                # e.g. "Full episode: The Example Podcast"
    credit_link: str = ""
    revenue_terms: str = ""
    payout_per_1k: float | None = None    # clipping campaigns: USD per 1,000 views
    expires_at: datetime | None = None
    active: bool = True

    # Discovery and ingest behaviour.
    niche: str = ""
    language: str = "auto"               # whisper language code or "auto"
    caption_script: str = "native"       # native | latin (Hinglish-style romanisation)
    watch: bool = True
    watch_interval_hours: int = 6
    auto_ingest: bool = True
    min_viral_score: float = 1.5         # only auto-clip videos that beat the channel median by this factor
    max_video_age_days: int = 30
    max_ingest_per_check: int = 2
    min_duration_s: int = 300            # skip anything shorter (already a short)
    cta_keyword: str = ""                # "Comment KEYWORD" end card + DM funnel (e.g. "LINK")
    # Hands-off mode: post the best clips that pass quality checks without waiting for review.
    auto_publish: bool = False
    auto_publish_top: int = 3
    target_accounts: list = JSONField([])

    last_checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- pipeline

VIDEO_STAGES = ("discovered", "queued", "downloading", "transcribing", "analyzing", "mining", "rendering",
                "ready", "failed", "skipped")


class Video(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id", index=True)
    external_id: str = Field(index=True)
    url: str = ""
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    description: str = ""
    thumbnail: str = ""
    published_at: datetime | None = None
    duration_s: float = 0
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    viral_score: float = 0               # views relative to channel median, adjusted for age
    velocity: float = 0                  # views per day

    status: str = "discovered"
    progress: float = 0
    stage_message: str = ""
    error: str = ""
    file_path: str = ""
    audio_path: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0
    content_hash: str = Field(default="", index=True)
    language: str = ""
    transcript_path: str = ""
    timeline_path: str = ""
    source_deleted: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Moment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    start_s: float
    end_s: float
    tight_start_s: float | None = None
    tight_end_s: float | None = None
    text: str = ""
    title: str = ""
    hooks: list = JSONField([])          # [{"text": ..., "style": ...}]
    scores: dict = JSONField({})         # hook, standalone, emotion, surprise, value, payoff, quotable
    heuristic_score: float = 0
    llm_score: float = 0
    predicted_score: float = 0
    final_score: float = 0
    hook_style: str = ""
    reason: str = ""
    punchline_s: list | None = JSONField(None)   # [start, end] of the strongest line, for cold opens
    emphasis: list = JSONField([])       # words to highlight
    emoji: dict = JSONField({})          # word -> emoji
    meta: dict = JSONField({})           # titles, descriptions, captions, tags
    features: dict = JSONField({})
    status: str = "candidate"            # candidate | selected | rendered | dropped
    created_at: datetime = Field(default_factory=utcnow)


CLIP_STATUSES = ("rendering", "qc_failed", "review", "approved", "rejected", "scheduled", "published", "failed")


class Clip(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    moment_id: int = Field(foreign_key="moment.id", index=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    source_id: int = Field(foreign_key="source.id", index=True)
    variant: str = "A"
    hook_text: str = ""
    hook_style: str = ""
    caption_style: str = "karaoke"
    length_bucket: str = "medium"
    opening: str = "straight"            # straight | cold_open | narrated
    edit_preset: str = "clean"           # clean | hype | minimal
    layout: str = "auto"
    music: str = ""
    start_s: float = 0
    end_s: float = 0
    edl: list = JSONField([])            # [[src_start, src_end], ...]
    caption_edits: dict = JSONField({})  # word index -> replacement text
    file_path: str = ""
    thumb_path: str = ""
    duration_s: float = 0
    render_stats: dict = JSONField({})
    qc_passed: bool = False
    qc: list = JSONField([])             # [{"name", "passed", "detail"}]
    status: str = "rendering"
    error: str = ""
    predicted_score: float = 0
    predicted_views: float = 0
    meta: dict = JSONField({})
    target_accounts: list = JSONField([])
    reviewed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- pages and publishing

class Account(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    platform: str                        # youtube | instagram
    name: str = ""
    handle: str = ""
    external_id: str = Field(default="", index=True)
    avatar: str = ""
    niche: str = ""
    language: str = "en"
    voice: str = "Friendly, short, a little witty. No hashtags in replies."
    cadence_per_day: int = 3
    time_slots: list = JSONField(["09:00", "13:00", "19:30"])
    timezone: str = "Asia/Kolkata"
    min_gap_minutes: int = 120
    learn_slots: bool = True
    privacy_status: str = "public"       # youtube only: public | unlisted | private
    default_hashtags: str = ""
    credentials: dict = JSONField({})    # tokens; never sent to the browser
    token_expires_at: datetime | None = None
    google_project: str = "default"      # which OAuth client (quota bucket) this channel uses
    auto_reply_categories: list = JSONField([])
    auto_hide_spam: bool = True
    # Comment-to-DM funnels: [{"keyword": "LINK", "message": "Here you go: https://…", "public_reply": "Sent! Check DMs 📩"}]
    dm_funnels: list = JSONField([])
    active: bool = True
    status: str = "connected"            # connected | expired | error
    status_detail: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Post(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    clip_id: int = Field(foreign_key="clip.id", index=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    platform: str = ""
    scheduled_at: datetime = Field(index=True)
    status: str = "scheduled"            # scheduled | publishing | published | failed | canceled
    attempts: int = 0
    error: str = ""
    title: str = ""
    description: str = ""
    caption: str = ""
    tags: list = JSONField([])
    external_id: str = ""
    container_id: str = ""
    url: str = ""
    published_at: datetime | None = None
    slot_hour: int = -1

    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    reach: int = 0
    avg_view_duration: float = 0
    avg_view_pct: float = 0
    followers_gained: int = 0
    earnings: float = 0
    earnings_manual: bool = False
    views_24h: int | None = None
    views_72h: int | None = None
    retention: list = JSONField([])
    last_metrics_at: datetime | None = None
    alerted_views: int = 0
    learned: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class MetricSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="post.id", index=True)
    taken_at: datetime = Field(default_factory=utcnow)
    hours: float = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    reach: int = 0
    avg_view_duration: float = 0
    avg_view_pct: float = 0
    followers_gained: int = 0


# ---------------------------------------------------------------- community

COMMENT_CATEGORIES = ("question", "praise", "criticism", "spam", "hate", "wants_full_episode", "other")


class Comment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="post.id", index=True)
    account_id: int = Field(foreign_key="account.id", index=True)
    platform: str = ""
    external_id: str = Field(index=True)
    author: str = ""
    text: str = ""
    published_at: datetime | None = None
    like_count: int = 0
    category: str = ""
    confidence: float = 0
    needs_attention: bool = False
    draft_reply: str = ""
    reply_status: str = "new"            # new | drafted | approved | posted | skipped | hidden | failed
    reply_external_id: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- discovery

class Discovery(SQLModel, table=True):
    """A viral video found by the trend scout. It can't be clipped until a rights record covers it."""
    id: int | None = Field(default=None, primary_key=True)
    external_id: str = Field(index=True)
    url: str = ""
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    channel_url: str = ""
    thumbnail: str = ""
    query: str = ""
    published_at: datetime | None = None
    duration_s: float = 0
    view_count: int = 0
    viral_score: float = 0
    velocity: float = 0
    status: str = "new"                  # new | dismissed | claimed
    source_id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------- ops

class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    lane: str = "light"                  # heavy (one at a time) | light
    payload: dict = JSONField({})
    status: str = Field(default="queued", index=True)   # queued | running | done | failed | canceled
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    progress: float = 0
    message: str = ""
    error: str = ""
    dedupe_key: str = Field(default="", index=True)
    run_after: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Event(SQLModel, table=True):
    """Activity feed and notifications (also mirrored to Telegram and the Mac app)."""
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)        # clips_ready | viral | post_failed | token_expiring | replies_waiting | ...
    level: str = "info"                  # info | success | warning | error
    title: str = ""
    body: str = ""
    data: dict = JSONField({})
    read: bool = False
    telegram_sent: bool = False
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=utcnow)


class BanditArm(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    scope: str = Field(index=True)       # "global" or "account:<id>"
    dimension: str = Field(index=True)   # hook_style | caption_style | length_bucket | opening | slot_hour
    arm: str = ""
    alpha: float = 1.0
    beta: float = 1.0
    pulls: int = 0
    updated_at: datetime = Field(default_factory=utcnow)
