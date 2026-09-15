"""YouTube: OAuth for multiple channels, Shorts upload, stats, analytics, comments (official APIs only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, logs, settings
from ..db import save
from ..models import Account, utcnow

log = logs.get("youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
_pending_flows: dict[str, object] = {}

# Loopback OAuth on http://localhost is fine; oauthlib needs to be told.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class QuotaExceeded(Exception):
    pass


def redirect_uri() -> str:
    return f"{config.base_url()}/api/oauth/youtube/callback"


def client_config(project: str = "default") -> dict:
    secrets = settings.get("youtube_client_secrets") or {}
    cfg = secrets.get(project) or (next(iter(secrets.values())) if secrets else None)
    if not cfg:
        raise RuntimeError("Add a Google OAuth client (client_secret.json) in Settings → YouTube first")
    return cfg


def start_auth(project: str = "default") -> str:
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(client_config(project), scopes=SCOPES, redirect_uri=redirect_uri())
    url, state = flow.authorization_url(access_type="offline", prompt="consent select_account",
                                        include_granted_scopes="true")
    _pending_flows[state] = (flow, project)
    return url


def finish_auth(state: str, code: str) -> list[Account]:
    from googleapiclient.discovery import build

    entry = _pending_flows.pop(state, None)
    if entry is None:
        raise RuntimeError("OAuth session expired; start the connection again")
    flow, project = entry
    flow.fetch_token(code=code)
    creds = flow.credentials
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
    out = []
    from sqlmodel import select

    from ..db import session

    for ch in resp.get("items", []):
        with session() as s:
            acc = s.exec(select(Account).where(Account.platform == "youtube", Account.external_id == ch["id"])).first()
        acc = acc or Account(platform="youtube", external_id=ch["id"])
        acc.name = ch["snippet"]["title"]
        acc.handle = ch["snippet"].get("customUrl", "")
        acc.avatar = ch["snippet"].get("thumbnails", {}).get("default", {}).get("url", "")
        acc.credentials = json.loads(creds.to_json())
        acc.google_project = project
        acc.status, acc.status_detail = "connected", ""
        acc.token_expires_at = None
        out.append(save(acc))
    if not out:
        raise RuntimeError("That Google account has no YouTube channel")
    return out


def _creds(acc: Account):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(acc.credentials, SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            acc.credentials = json.loads(creds.to_json())
            save(acc)
        else:
            raise RuntimeError("YouTube token expired; reconnect the channel")
    return creds


def service(acc: Account, api: str = "youtube", version: str = "v3"):
    from googleapiclient.discovery import build

    return build(api, version, credentials=_creds(acc), cache_discovery=False)


# ---------------------------------------------------------------- quota (per Google project, resets at midnight Pacific)

def _quota_key(project: str) -> str:
    day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    return f"yt_quota:{project}:{day}"


def quota_used(project: str) -> int:
    return int(settings.get(_quota_key(project)) or 0)


def spend_quota(project: str, units: int) -> None:
    settings.set(_quota_key(project), quota_used(project) + units)


def quota_left(project: str) -> int:
    return int(settings.get("youtube_quota_per_project")) - quota_used(project)


def seconds_until_quota_reset() -> float:
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (nxt - now).total_seconds()


# ---------------------------------------------------------------- upload

def upload(acc: Account, path: str, title: str, description: str, tags: list[str], language: str = "en",
           progress=None) -> dict:
    from googleapiclient.http import MediaFileUpload

    cost = int(settings.get("youtube_upload_cost"))
    if quota_left(acc.google_project) < cost:
        raise QuotaExceeded(f"YouTube quota for project '{acc.google_project}' is used up for today")
    yt = service(acc)
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": "22", "defaultLanguage": language, "defaultAudioLanguage": language},
        "status": {"privacyStatus": acc.privacy_status or "public", "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False},
    }
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=True)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status and progress:
            progress(status.progress(), "uploading to YouTube")
    spend_quota(acc.google_project, cost)
    vid = resp["id"]
    return {"id": vid, "url": f"https://www.youtube.com/shorts/{vid}",
            "privacy": resp.get("status", {}).get("privacyStatus")}


# ---------------------------------------------------------------- metrics

def video_stats(acc: Account, ids: list[str]) -> dict[str, dict]:
    yt = service(acc)
    out = {}
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        spend_quota(acc.google_project, 1)
        for it in resp.get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                             "comments": int(st.get("commentCount", 0))}
    return out


def analytics(acc: Account, ids: list[str], start: datetime) -> dict[str, dict]:
    """Average view duration/percentage and subscribers gained per video (YouTube Analytics lags ~1-2 days)."""
    if not ids:
        return {}
    ya = service(acc, "youtubeAnalytics", "v2")
    out = {}
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        try:
            resp = ya.reports().query(
                ids="channel==MINE", startDate=start.strftime("%Y-%m-%d"),
                endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                metrics="views,averageViewDuration,averageViewPercentage,subscribersGained,shares",
                dimensions="video", filters="video==" + ",".join(chunk)).execute()
        except Exception as exc:  # noqa: BLE001
            log.info("analytics not available yet: %s", exc)
            return out
        cols = [h["name"] for h in resp.get("columnHeaders", [])]
        for row in resp.get("rows", []) or []:
            d = dict(zip(cols, row))
            out[d["video"]] = {"avg_view_duration": float(d.get("averageViewDuration", 0)),
                               "avg_view_pct": float(d.get("averageViewPercentage", 0)),
                               "followers_gained": int(d.get("subscribersGained", 0)),
                               "shares": int(d.get("shares", 0))}
    return out


def retention(acc: Account, video_id: str, start: datetime) -> list[list[float]]:
    ya = service(acc, "youtubeAnalytics", "v2")
    try:
        resp = ya.reports().query(ids="channel==MINE", startDate=start.strftime("%Y-%m-%d"),
                                  endDate=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                  metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
                                  filters=f"video=={video_id}").execute()
    except Exception:  # noqa: BLE001
        return []
    return [[float(r[0]), float(r[1])] for r in resp.get("rows", []) or []]


# ---------------------------------------------------------------- comments (own videos only)

def comments(acc: Account, video_id: str, limit: int = 100) -> list[dict]:
    yt = service(acc)
    resp = yt.commentThreads().list(part="snippet", videoId=video_id, maxResults=min(100, limit), order="time",
                                    textFormat="plainText").execute()
    spend_quota(acc.google_project, 1)
    out = []
    for it in resp.get("items", []):
        top = it["snippet"]["topLevelComment"]
        sn = top["snippet"]
        if sn.get("authorChannelId", {}).get("value") == acc.external_id:
            continue  # our own comment
        out.append({"id": top["id"], "author": sn.get("authorDisplayName", ""), "text": sn.get("textDisplay", ""),
                    "published_at": sn.get("publishedAt"), "likes": int(sn.get("likeCount", 0)),
                    "replied": int(it["snippet"].get("totalReplyCount", 0)) > 0})
    return out


def reply(acc: Account, comment_id: str, text: str) -> str:
    yt = service(acc)
    resp = yt.comments().insert(part="snippet", body={"snippet": {"parentId": comment_id, "textOriginal": text}}).execute()
    spend_quota(acc.google_project, 50)
    return resp["id"]


def hide(acc: Account, comment_id: str) -> None:
    yt = service(acc)
    yt.comments().setModerationStatus(id=comment_id, moderationStatus="heldForReview").execute()
    spend_quota(acc.google_project, 50)


def mark_expired(acc: Account, detail: str) -> None:
    acc.status, acc.status_detail = "expired", detail[:300]
    acc.token_expires_at = utcnow()
    save(acc)
