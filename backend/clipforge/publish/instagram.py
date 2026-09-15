"""Instagram (Professional accounts) through the official Instagram Platform API.

Two ways to connect, both supported:
  facebook  - Instagram API with Facebook Login (graph.facebook.com). Reels upload
              straight from the Mac via resumable upload: no hosting needed.
  instagram - Instagram API with Instagram Login (graph.instagram.com). Instagram
              must fetch the video from a public URL, so a temporary tunnel is opened.
"""

from __future__ import annotations

import os
import time
from datetime import timedelta

import httpx

from .. import logs, settings
from ..db import save
from ..models import Account, utcnow

log = logs.get("instagram")


class IGError(Exception):
    def __init__(self, message: str, code: int | None = None, retriable: bool = False):
        super().__init__(message)
        self.code = code
        self.retriable = retriable


def _ver() -> str:
    return settings.get("instagram_graph_version") or "v23.0"


def _host(acc: Account) -> str:
    return "graph.facebook.com" if acc.credentials.get("host") == "facebook" else "graph.instagram.com"


def _call(method: str, url: str, token: str, **kw) -> dict:
    params = kw.pop("params", {}) or {}
    params["access_token"] = token
    r = httpx.request(method, url, params=params, timeout=60, **kw)
    try:
        data = r.json()
    except ValueError:
        data = {"error": {"message": r.text[:300]}}
    if r.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        code = err.get("code")
        raise IGError(err.get("message", f"HTTP {r.status_code}"), code,
                      retriable=bool(err.get("is_transient")) or r.status_code >= 500 or code in (4, 17, 32, 613))
    return data


def api(acc: Account, method: str, path: str, **kw) -> dict:
    return _call(method, f"https://{_host(acc)}/{_ver()}/{path.lstrip('/')}", acc.credentials["access_token"], **kw)


# ---------------------------------------------------------------- connecting

def connect_with_token(token: str, ig_user_id: str = "") -> list[Account]:
    """Paste a long-lived token from the Meta developer dashboard. Works for either login type."""
    from sqlmodel import select

    from ..db import session

    found: list[dict] = []
    try:
        me = _call("GET", f"https://graph.instagram.com/{_ver()}/me", token,
                   params={"fields": "user_id,username,name,profile_picture_url,account_type"})
        found.append({"host": "instagram", "id": str(me.get("user_id") or me.get("id")), "username": me.get("username", ""),
                      "name": me.get("name") or me.get("username", ""), "avatar": me.get("profile_picture_url", ""),
                      "token": token})
    except IGError:
        pages = _call("GET", f"https://graph.facebook.com/{_ver()}/me/accounts", token,
                      params={"fields": "name,access_token,instagram_business_account{id,username,name,profile_picture_url}"})
        for pg in pages.get("data", []):
            ig = pg.get("instagram_business_account")
            if not ig:
                continue
            if ig_user_id and ig["id"] != ig_user_id:
                continue
            found.append({"host": "facebook", "id": ig["id"], "username": ig.get("username", ""),
                          "name": ig.get("name") or ig.get("username", ""), "avatar": ig.get("profile_picture_url", ""),
                          "token": pg.get("access_token") or token, "page": pg.get("name", "")})
    if not found:
        raise RuntimeError("No Instagram Professional account found for that token")
    out = []
    for f in found:
        with session() as s:
            acc = s.exec(select(Account).where(Account.platform == "instagram", Account.external_id == f["id"])).first()
        acc = acc or Account(platform="instagram", external_id=f["id"])
        acc.name, acc.handle, acc.avatar = f["name"], f["username"], f["avatar"]
        acc.credentials = {"access_token": f["token"], "host": f["host"], "issued_at": utcnow().isoformat()}
        # Instagram Login long-lived tokens last 60 days; page tokens from Facebook Login don't expire.
        acc.token_expires_at = utcnow() + timedelta(days=59) if f["host"] == "instagram" else None
        acc.status, acc.status_detail = "connected", ""
        out.append(save(acc))
    return out


def oauth_url(redirect_uri: str, state: str) -> str:
    app_id = settings.get("instagram_app_id")
    if not app_id:
        raise RuntimeError("Set your Instagram app ID and secret in Settings first")
    scopes = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_insights"
    return (f"https://www.instagram.com/oauth/authorize?client_id={app_id}&redirect_uri={httpx.QueryParams({'r': redirect_uri})['r']}"
            f"&response_type=code&scope={scopes}&state={state}")


def oauth_finish(code: str, redirect_uri: str) -> list[Account]:
    app_id, secret = settings.get("instagram_app_id"), settings.get("instagram_app_secret")
    r = httpx.post("https://api.instagram.com/oauth/access_token", timeout=30, data={
        "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
        "redirect_uri": redirect_uri, "code": code})
    short = r.json()
    if "access_token" not in short:
        raise RuntimeError(short.get("error_message") or str(short))
    long = _call("GET", "https://graph.instagram.com/access_token", short["access_token"],
                 params={"grant_type": "ig_exchange_token", "client_secret": secret})
    return connect_with_token(long["access_token"])


def refresh_token(acc: Account) -> bool:
    if acc.credentials.get("host") != "instagram":
        return True
    try:
        data = _call("GET", "https://graph.instagram.com/refresh_access_token", acc.credentials["access_token"],
                     params={"grant_type": "ig_refresh_token"})
    except IGError as exc:
        acc.status, acc.status_detail = "expired", str(exc)[:300]
        save(acc)
        return False
    acc.credentials = {**acc.credentials, "access_token": data["access_token"], "issued_at": utcnow().isoformat()}
    acc.token_expires_at = utcnow() + timedelta(seconds=int(data.get("expires_in", 5_184_000)))
    acc.status, acc.status_detail = "connected", ""
    save(acc)
    return True


# ---------------------------------------------------------------- publishing

def publishing_quota(acc: Account) -> tuple[int, int]:
    try:
        d = api(acc, "GET", f"{acc.external_id}/content_publishing_limit", params={"fields": "quota_usage,config"})
        row = (d.get("data") or [{}])[0]
        return int(row.get("quota_usage", 0)), int((row.get("config") or {}).get("quota_total", 100))
    except IGError:
        return 0, int(settings.get("instagram_daily_limit"))


def _wait_ready(acc: Account, container: str, progress=None, timeout_s: int = 900) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = api(acc, "GET", container, params={"fields": "status_code,status"})
        code = st.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise IGError(f"Instagram couldn't process the video: {st.get('status', code)}")
        if progress:
            progress(min(0.95, 0.5 + (time.time() - t0) / timeout_s / 2), "Instagram is processing the reel")
        time.sleep(10)
    raise IGError("timed out waiting for Instagram to process the reel", retriable=True)


def publish_reel(acc: Account, clip_id: int, path: str, caption: str, thumb_offset_ms: int = 600,
                 progress=None) -> dict:
    ig = acc.external_id
    base = {"media_type": "REELS", "caption": caption[:2200], "share_to_feed": "true",
            "thumb_offset": str(thumb_offset_ms)}
    if acc.credentials.get("host") == "facebook":
        created = api(acc, "POST", f"{ig}/media", params={**base, "upload_type": "resumable"})
        container, uri = created["id"], created.get("uri") or \
            f"https://rupload.facebook.com/ig-api-upload/{_ver()}/{created['id']}"
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            r = httpx.post(uri, content=f.read(), timeout=600, headers={
                "Authorization": f"OAuth {acc.credentials['access_token']}", "offset": "0", "file_size": str(size)})
        if r.status_code >= 400 or not r.json().get("success", True):
            raise IGError(f"upload failed: {r.text[:300]}", retriable=True)
        if progress:
            progress(0.5, "uploaded, waiting for processing")
        _wait_ready(acc, container, progress)
    else:
        from .hosting import Tunnel

        with Tunnel() as tunnel:
            created = api(acc, "POST", f"{ig}/media", params={**base, "video_url": tunnel.media_url(clip_id)})
            container = created["id"]
            if progress:
                progress(0.3, "Instagram is fetching the video")
            _wait_ready(acc, container, progress)
    pub = api(acc, "POST", f"{ig}/media_publish", params={"creation_id": container})
    media_id = pub["id"]
    try:
        info = api(acc, "GET", media_id, params={"fields": "permalink,shortcode"})
        url = info.get("permalink", "")
    except IGError:
        url = ""
    return {"id": media_id, "url": url, "container": container}


# ---------------------------------------------------------------- insights & comments (own media only)

INSIGHT_METRICS = ["views", "reach", "saved", "shares", "likes", "comments", "total_interactions",
                   "ig_reels_avg_watch_time", "follows"]


def insights(acc: Account, media_id: str) -> dict:
    metrics = list(INSIGHT_METRICS)
    for _ in range(4):
        try:
            d = api(acc, "GET", f"{media_id}/insights", params={"metric": ",".join(metrics)})
            out = {}
            for row in d.get("data", []):
                vals = row.get("values") or [{}]
                out[row["name"]] = vals[0].get("value", 0) if vals else row.get("total_value", {}).get("value", 0)
            return out
        except IGError as exc:
            msg = str(exc)
            bad = [m for m in metrics if m in msg]
            if not bad:
                raise
            metrics = [m for m in metrics if m not in bad]
    return {}


def comments(acc: Account, media_id: str) -> list[dict]:
    d = api(acc, "GET", f"{media_id}/comments", params={"fields": "id,text,username,timestamp,like_count,replies{id}", "limit": 50})
    out = []
    for c in d.get("data", []):
        if c.get("username") == acc.handle:
            continue
        out.append({"id": c["id"], "author": c.get("username", ""), "text": c.get("text", ""),
                    "published_at": c.get("timestamp"), "likes": int(c.get("like_count", 0)),
                    "replied": bool((c.get("replies") or {}).get("data"))})
    return out


def reply(acc: Account, comment_id: str, text: str) -> str:
    return api(acc, "POST", f"{comment_id}/replies", params={"message": text})["id"]


def private_reply(acc: Account, comment_id: str, text: str) -> str:
    """DM the person who left a comment (Instagram 'private replies', allowed within 7 days of the comment)."""
    body = {"recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}}
    r = httpx.post(f"https://{_host(acc)}/{_ver()}/{acc.external_id}/messages", json=body, timeout=30,
                   params={"access_token": acc.credentials["access_token"]})
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        raise IGError((data.get("error") or {}).get("message", r.text[:200]))
    return data.get("message_id", "")


def hide(acc: Account, comment_id: str) -> None:
    api(acc, "POST", comment_id, params={"hide": "true"})
