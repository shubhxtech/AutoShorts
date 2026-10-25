"""User settings stored in the database, with defaults.

Secret values are never returned to the dashboard in full; see `public_settings`.
"""

from __future__ import annotations

import os
from typing import Any

from sqlmodel import select

from .db import session
from .models import Setting, utcnow

DEFAULTS: dict[str, Any] = {
    # Local LLM (MLX, runs on-device). The fast model is used for bulk scoring and comment triage.
    "llm_backend": "auto",                # auto | mlx (Apple Silicon) | server (Ollama / LM Studio / llama.cpp)
    "llm_server_url": "http://localhost:11434/v1",
    "llm_server_model": "qwen3:8b",
    "llm_model": "mlx-community/Qwen3.5-9B-MLX-4bit",
    "llm_fast_model": "mlx-community/Qwen3.5-9B-MLX-4bit",
    "llm_enabled": True,
    # Transcription
    "whisper_model": "mlx-community/whisper-large-v3-turbo",
    "hf_token": "",                       # pyannote diarization
    "diarization": False,
    "allowed_languages": ["en", "hi"],    # anything else Whisper detects is mapped to one of these (Urdu -> Hindi)
    "forced_alignment": True,             # wav2vec2 re-timing of words in chosen clips (English)
    # Mining
    "moments_per_video": 6,
    "min_moment_s": 20,
    "max_moment_s": 70,
    "llm_candidates_per_video": 36,
    "min_final_score": 5.5,
    # Rendering
    "variants_per_moment": 3,
    "fps": 30,
    "encoder": "auto",                    # auto | videotoolbox | nvenc | qsv | amf | x264
    "video_bitrate": "10M",
    "jump_cuts": True,
    "max_pause_s": 0.45,
    "punch_zooms": True,
    "progress_bar": True,
    "color_grade": True,
    "sfx": True,                          # only where something visual happens (see edit presets)
    "unload_idle_minutes": 3,             # drop cached models after this many idle minutes (0 = keep them)
    "faceless_music": True,               # faceless videos get a generated (royalty-free) bed by default
    "music_enabled": False,               # top podcast clips rarely use music; enable once you add licensed tracks
    "music_volume_db": -24,
    "edit_preset": "auto",                # auto (bandit picks per variant) | clean | hype | minimal
    "hook_seconds": 3.0,                  # how long the opening text stays (research: readable in ~1 glance, 3-5 s)
    "hook_style": "native",               # native | bold | dark | none (off)
    "brand_color": "#FFD400",
    "caption_font": "",                   # PostScript name or empty for style default
    "end_card": True,
    "ai_broll": 1,                        # AI-drawn cutaways per clip (0 = off; each takes ~1 min to draw once)
    "broll_style": "",                    # image style for b-roll (empty = cinematic)
    "narrated_intros": False,             # offline macOS voice reads the hook over a held frame
    "tts_voice": "",                      # voice id for narrated intros, e.g. "design:documentary" (empty = default)
    # Quality checks
    "target_lufs": -14.0,
    "max_duration_youtube": 180,
    "max_duration_instagram": 90,
    "min_duration": 12,
    # Publishing
    "auto_schedule_on_approve": True,
    "youtube_client_secrets": {},         # project name -> client config JSON
    "youtube_quota_per_project": 10000,
    "youtube_upload_cost": 1600,
    "instagram_app_id": "",
    "instagram_app_secret": "",
    "instagram_graph_version": "v23.0",
    "instagram_daily_limit": 50,
    "public_base_url": "",                # optional tunnel URL if resumable upload is unavailable
    "youtube_api_key": "",                # optional: faster discovery stats
    # Notifications
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "viral_thresholds": [10000, 100000, 1000000],
    # Ops
    "automation_paused": False,
    "keep_source_days": 14,
    "keep_rejected_days": 3,
    "keep_published_days": 30,
    "metrics_interval_minutes": 60,
    "comments_interval_minutes": 30,
    # Faceless studio
    "faceless_quality": "best",           # best (768x1344, 8 steps) | fast (576x1024, 6 steps, ~2x quicker)
    # Downloads
    "ytdlp_cookies_browser": "",          # e.g. "chrome" or "safari" if YouTube asks to sign in
    # Trend scout
    "scout_queries": [],
    "scout_max_age_days": 14,
}

SECRET_KEYS = {"hf_token", "instagram_app_secret", "telegram_bot_token", "youtube_api_key"}

# Environment variables override empty stored values, so a .env file works for development.
ENV_KEYS = {
    "hf_token": "HF_TOKEN",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
    "telegram_chat_id": "TELEGRAM_CHAT_ID",
    "youtube_api_key": "YOUTUBE_API_KEY",
    "public_base_url": "AUTOSHORTS_PUBLIC_URL",
    "llm_backend": "AUTOSHORTS_LLM_BACKEND",           # Docker: "server", pointing at the Ollama container
    "llm_server_url": "AUTOSHORTS_LLM_URL",
    "llm_server_model": "AUTOSHORTS_LLM_MODEL",
}


def get(key: str, default: Any = None) -> Any:
    with session() as s:
        row = s.get(Setting, key)
    if row is not None and row.value not in (None, ""):
        return row.value
    env = ENV_KEYS.get(key)
    if env and os.environ.get(env):
        return os.environ[env]
    if row is not None:
        return row.value
    return DEFAULTS.get(key, default) if default is None else default


def set(key: str, value: Any) -> None:
    with session() as s:
        row = s.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
        else:
            row.value = value
            row.updated_at = utcnow()
        s.add(row)
        s.commit()


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULTS)
    with session() as s:
        for row in s.exec(select(Setting)).all():
            if row.key in DEFAULTS:
                out[row.key] = row.value
    for k, env in ENV_KEYS.items():
        if out.get(k) in (None, "") and os.environ.get(env):
            out[k] = os.environ[env]
    return out


def _mask(v: Any) -> str:
    if not v:
        return ""
    s = str(v)
    return "••••" + s[-4:] if len(s) > 8 else "••••"


def public_settings() -> dict[str, Any]:
    out = all_settings()
    for k in SECRET_KEYS:
        out[k] = _mask(out.get(k))
    out["youtube_client_secrets"] = {
        name: {"client_id": (cfg.get("web") or cfg.get("installed") or {}).get("client_id", "")}
        for name, cfg in (out.get("youtube_client_secrets") or {}).items()
    }
    return out


def update(values: dict[str, Any]) -> None:
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        # The dashboard sends masked secrets back unchanged; ignore those.
        if k in SECRET_KEYS and isinstance(v, str) and v.startswith("••••"):
            continue
        set(k, v)
