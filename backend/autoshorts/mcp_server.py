"""MCP server: lets an AI assistant (Claude Desktop, Claude Code, Cursor, ...) drive AutoShorts.

It is a thin client of the running engine's HTTP API, so the rights gate, the review queue and every other rule
apply exactly as they do in the dashboard. Run `autoshorts up` first, then point your assistant at `autoshorts mcp`.
Nothing is published without an approval: approving is a tool the assistant has to call, and it asks you first.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from . import config

try:                                                    # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

INSTRUCTIONS = """AutoShorts turns long videos into Shorts/Reels and makes faceless videos, all on this computer.
Typical flow: make_clips (or make_faceless_video) -> wait, checking status -> list_clips -> show the user the
clips -> approve_clips only after the user says which ones. Only clip videos the user owns or has permission for."""

mcp = _Server("autoshorts", instructions=INSTRUCTIONS)


def _base() -> str:
    return os.environ.get("AUTOSHORTS_URL", f"http://127.0.0.1:{config.port()}").rstrip("/")


def _token() -> str:
    from .api.security import token  # same per-install file the engine reads

    return token()


def _call(method: str, path: str, **kw: Any) -> Any:
    try:
        r = httpx.request(method, f"{_base()}/api{path}", headers={"X-AutoShorts-Token": _token()}, timeout=60, **kw)
    except httpx.ConnectError as exc:
        raise ToolError("AutoShorts isn't running. Start it with `autoshorts up` (or open the app).") from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise ToolError(f"AutoShorts said: {detail}")
    return r.json()


def _clip(c: dict) -> dict:
    """The fields an assistant needs to talk about a clip, without transcripts and render internals."""
    return {k: c.get(k) for k in ("id", "status", "hook_text", "youtube_title", "predicted_score", "duration_s",
                                  "video_title", "variant", "thumb_url", "url")} | {"dashboard": f"{_base()}/clips/{c.get('id')}"}


@mcp.tool()
def status() -> dict:
    """What AutoShorts is doing: clips waiting for review, videos processing, posts scheduled, failures."""
    s = _call("GET", "/summary")
    busy = _call("GET", "/videos", params={"status": "queued,downloading,transcribing,analyzing,mining,rendering"})
    return {"summary": s, "processing": [{"id": v["id"], "title": v.get("title"), "status": v["status"],
                                          "progress": v.get("progress"), "stage": v.get("stage_message")} for v in busy]}


@mcp.tool()
def make_clips(url: str, i_own_or_have_permission: bool, rights_type: str = "own", proof_url: str = "",
               credit_text: str = "", niche: str = "", hinglish_captions: bool = False) -> dict:
    """Find the best moments in a long video (YouTube link or local file path) and edit them into vertical clips.

    Only for videos the user owns or has permission to clip: set i_own_or_have_permission only if the user said so.
    rights_type: own | licensed | creator_permission | clipping_campaign (anything but own needs proof_url).
    Clips land in the review queue; nothing is posted.
    """
    return _call("POST", "/quick", json={"url": url, "attest": i_own_or_have_permission, "rights_type": rights_type,
                                         "proof_url": proof_url, "credit_text": credit_text, "niche": niche,
                                         "caption_script": "latin" if hinglish_captions else "native"})


@mcp.tool()
def faceless_formats() -> dict:
    """Formats, visual styles and voices available for faceless videos."""
    o = _call("GET", "/faceless/options")
    return {"formats": [{"id": f["id"], "name": f["name"], "about": f["blurb"], "seconds": f["seconds"]} for f in o["formats"]],
            "styles": [s["id"] for s in o["styles"]], "voices": o["voices"]}


@mcp.tool()
def make_faceless_video(topic: str = "", script_text: str = "", format: str = "story", style: str = "doodle",
                        language: str = "English", seconds: int = 45, voice: str = "", count: int = 1) -> dict:
    """Make a faceless narrated video from a topic (or a ready script). count makes up to 10 variations at once.

    language: English or Hindi. See faceless_formats for format, style and voice ids.
    """
    body: dict[str, Any] = {"topic": topic, "script_text": script_text, "format": format, "style": style,
                            "language": language, "seconds": seconds, "count": count}
    if voice:
        body["voice"] = voice
    return _call("POST", "/faceless", json=body)


@mcp.tool()
def list_clips(status: str = "review", limit: int = 20) -> list[dict]:
    """Clips by status (review, approved, published, rejected, qc_failed), best predicted first."""
    return [_clip(c) for c in _call("GET", "/clips", params={"status": status, "limit": limit})]


@mcp.tool()
def edit_clip(clip_id: int, hook_text: str | None = None, youtube_title: str | None = None,
              instagram_caption: str | None = None) -> dict:
    """Change a clip's on-screen hook, YouTube title or Instagram caption. Text edits don't need a re-render."""
    body = {k: v for k, v in {"hook_text": hook_text, "youtube_title": youtube_title,
                              "instagram_caption": instagram_caption}.items() if v is not None}
    return _clip(_call("PATCH", f"/clips/{clip_id}", json=body | {"rerender": False}))


@mcp.tool()
def approve_clips(clip_ids: list[int], account_ids: list[int] | None = None) -> list:
    """Approve clips: they get their final render and are scheduled to the connected pages.

    Publishes to the user's accounts, so only call this after the user picked these clips.
    """
    return _call("POST", "/clips/approve", json={"ids": clip_ids, "accounts": account_ids or []})


@mcp.tool()
def reject_clips(clip_ids: list[int]) -> dict:
    """Reject clips so they leave the review queue."""
    return _call("POST", "/clips/reject", json={"ids": clip_ids})


@mcp.tool()
def list_accounts() -> list[dict]:
    """Connected YouTube and Instagram pages (ids are used by approve_clips)."""
    return [{"id": a["id"], "platform": a["platform"], "name": a["name"], "active": a.get("active")}
            for a in _call("GET", "/accounts")]


@mcp.tool()
def scheduled_posts(days: int = 14) -> list[dict]:
    """Posts scheduled or published in the last/next few days."""
    return [{"id": p["id"], "status": p["status"], "platform": p["platform"], "account": p.get("account_name"),
             "at": p.get("scheduled_at"), "title": p.get("title") or p.get("hook_text"), "url": p.get("url"),
             "views": p.get("views")} for p in _call("GET", "/posts", params={"days": days})]


def run() -> None:
    mcp.run("stdio")
