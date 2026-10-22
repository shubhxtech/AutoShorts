"""List the videos a registered source currently offers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .. import logs, settings
from ..models import Source
from .viral import Candidate

log = logs.get("ingest")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}


def _ts(value) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str) and re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, OSError):
        return None


def ytdlp_opts(**extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "ignoreerrors": True}
    browser = settings.get("ytdlp_cookies_browser")
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    opts.update(extra)
    return opts


def _channel_videos_url(url: str) -> str:
    u = url.rstrip("/")
    if re.search(r"youtube\.com/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)$", u):
        return u + "/videos"
    return u


def youtube_list(url: str, limit: int = 30) -> list[Candidate]:
    import yt_dlp

    opts = ytdlp_opts(extract_flat="in_playlist", playlistend=limit, skip_download=True,
                      extractor_args={"youtubetab": {"approximate_date": [""]}})
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(_channel_videos_url(url), download=False) or {}
    entries = info.get("entries") or ([info] if info.get("id") and not info.get("entries") else [])
    out: list[Candidate] = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        vid = e["id"]
        out.append(Candidate(
            external_id=vid,
            url=e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={vid}",
            title=e.get("title") or "",
            views=int(e.get("view_count") or 0),
            published_at=_ts(e.get("timestamp") or e.get("release_timestamp") or e.get("upload_date")),
            duration_s=float(e.get("duration") or 0),
            channel=e.get("channel") or info.get("channel") or info.get("uploader") or "",
            channel_id=e.get("channel_id") or info.get("channel_id") or "",
            thumbnail=(e.get("thumbnails") or [{}])[-1].get("url", "") if e.get("thumbnails") else "",
        ))
    _enrich_youtube_stats(out)
    return out


def _enrich_youtube_stats(cands: list[Candidate]) -> None:
    """Exact view counts and dates through the Data API when an API key is set (1 quota unit per 50 videos)."""
    key = settings.get("youtube_api_key")
    if not key or not cands:
        return
    for i in range(0, len(cands), 50):
        chunk = cands[i:i + 50]
        try:
            r = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=20, params={
                "part": "snippet,statistics,contentDetails", "id": ",".join(c.external_id for c in chunk), "key": key})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("YouTube Data API enrichment failed: %s", exc)
            return
        by_id = {it["id"]: it for it in r.json().get("items", [])}
        for c in chunk:
            it = by_id.get(c.external_id)
            if not it:
                continue
            c.views = int(it.get("statistics", {}).get("viewCount") or c.views)
            c.published_at = _ts(it.get("snippet", {}).get("publishedAt")) or c.published_at
            c.duration_s = _iso_duration(it.get("contentDetails", {}).get("duration")) or c.duration_s
            c.channel = it.get("snippet", {}).get("channelTitle") or c.channel


def _iso_duration(s: str | None) -> float:
    if not s:
        return 0
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def youtube_search(query: str, limit: int = 30) -> list[Candidate]:
    """Free search through yt-dlp (no API quota)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(ytdlp_opts(extract_flat=True, skip_download=True)) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        out.append(Candidate(
            external_id=e["id"], url=f"https://www.youtube.com/watch?v={e['id']}", title=e.get("title") or "",
            views=int(e.get("view_count") or 0),
            published_at=_ts(e.get("timestamp") or e.get("upload_date")),
            duration_s=float(e.get("duration") or 0),
            channel=e.get("channel") or e.get("uploader") or "", channel_id=e.get("channel_id") or "",
            thumbnail=(e.get("thumbnails") or [{}])[-1].get("url", "") if e.get("thumbnails") else "",
        ))
    _enrich_youtube_stats(out)
    return out


def rss_list(url: str, limit: int = 20) -> list[Candidate]:
    import feedparser

    feed = feedparser.parse(url)
    out = []
    for e in feed.entries[:limit]:
        media = next((l.get("href") for l in e.get("links", []) if l.get("rel") == "enclosure"), None)
        if not media and e.get("enclosures"):
            media = e.enclosures[0].get("href")
        if not media:
            continue
        dur = e.get("itunes_duration") or "0"
        secs = 0.0
        try:
            parts = [float(p) for p in str(dur).split(":")]
            for p in parts:
                secs = secs * 60 + p
        except ValueError:
            pass
        published = None
        if e.get("published_parsed"):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        image = (e.get("image") or {}).get("href") or (feed.feed.get("image") or {}).get("href", "")
        out.append(Candidate(external_id=e.get("id") or media, url=media, title=e.get("title", ""), views=0,
                             published_at=published, duration_s=secs, channel=feed.feed.get("title", ""),
                             thumbnail=image))
    return out


def local_list(folder: str) -> list[Candidate]:
    root = Path(folder).expanduser()
    out = []
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in VIDEO_EXTS | AUDIO_EXTS and p.is_file():
            st = p.stat()
            out.append(Candidate(external_id=f"file:{p.resolve()}:{st.st_size}", url=str(p.resolve()), title=p.stem,
                                 views=0, published_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc), duration_s=0))
    return out


def list_source(src: Source, limit: int = 30) -> list[Candidate]:
    kind = src.kind
    if kind in ("youtube_channel", "youtube_playlist"):
        return youtube_list(src.url, limit)
    if kind == "youtube_video":
        return youtube_list(src.url, 1)
    if kind == "rss":
        return rss_list(src.url, limit)
    if kind == "local_folder":
        return local_list(src.url)
    if kind == "direct_url":
        return [Candidate(external_id=src.url, url=src.url, title=Path(src.url.split("?")[0]).stem, views=0,
                          published_at=None, duration_s=0)]
    if kind == "drive":
        return [Candidate(external_id=src.url, url=src.url, title=src.name, views=0, published_at=None, duration_s=0)]
    if kind == "campaign":
        # A clipping campaign covers one or more creator channels and/or specific asset links.
        out: list[Candidate] = []
        for u in [src.url, *(src.extra_urls or [])]:
            if not u:
                continue
            if "youtube.com" in u or "youtu.be" in u:
                out.extend(youtube_list(u, limit))
            else:
                out.append(Candidate(external_id=u, url=u, title=Path(u.split("?")[0]).stem or src.name, views=0,
                                     published_at=None, duration_s=0))
        return out
    raise ValueError(f"unsupported source kind {kind!r}")
