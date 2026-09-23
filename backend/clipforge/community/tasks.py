"""Community manager for comments on our own posts only.

Never likes, follows or comments on anyone else's content.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import select

from .. import llm, logs
from ..db import save, session
from ..jobs.queue import JobContext, task
from ..models import Account, Clip, Comment, Post, Source, utcnow
from ..notify import events

log = logs.get("community")

Category = Literal["question", "praise", "criticism", "spam", "hate", "wants_full_episode", "other"]
SAFE_AUTO = {"wants_full_episode", "praise"}


class Triage(BaseModel):
    n: int = Field(description="comment number")
    category: Category
    needs_attention: bool = Field(description="true if a human should look (legal threat, crisis, partnership offer, factual dispute)")
    reply: str = Field(description="reply in the page's voice, max 2 short sentences; empty for spam or hate")


class TriageBatch(BaseModel):
    items: list[Triage]


SPAM = re.compile(r"(https?://|www\.|t\.me/|whats ?app|telegram|crypto|bitcoin|forex|check my (page|profile|channel)|"
                  r"dm me|follow me|sub ?4 ?sub|free followers|onlyfans)", re.I)
FULL_EP = re.compile(r"(full (episode|video|podcast|interview)|where.*(watch|full)|link\??$|part ?2|source\??$|"
                     r"which (podcast|episode)|name of (the )?(podcast|show))", re.I)
HATE = re.compile(r"\b(kill yourself|kys|retard|faggot|nigg|chutiya|madarchod|bhenchod)\b", re.I)


def heuristic(text: str) -> tuple[str, bool]:
    if SPAM.search(text):
        return "spam", False
    if HATE.search(text):
        return "hate", False
    if FULL_EP.search(text):
        return "wants_full_episode", False
    if "?" in text:
        return "question", False
    if re.search(r"(love|great|amazing|fire|🔥|❤️|best|so true|facts|💯|goat|legend)", text, re.I):
        return "praise", False
    if re.search(r"(wrong|stupid|fake|cringe|bad|clickbait|misleading|nonsense)", text, re.I):
        return "criticism", False
    return "other", False


def _template(category: str, src: Source | None) -> str:
    if category == "wants_full_episode" and src:
        link = f" {src.credit_link}" if src.credit_link else " (link in bio)"
        return f"Full episode: {src.credit_text or src.name}{link} 🎙"
    if category == "praise":
        return "Appreciate you! 🙌"
    return ""


def triage(comments: list[Comment], acc: Account, src: Source | None, context: str) -> None:
    results: dict[int, Triage] = {}
    if llm.available() and comments:
        listing = "\n".join(f"{i + 1}. @{c.author}: {c.text[:400]}" for i, c in enumerate(comments))
        system = (f"You manage comments for the social page '{acc.name}' ({acc.niche or 'general'} niche). "
                  f"Voice: {acc.voice}. Reply language: match the commenter (Hinglish if they write Hinglish). "
                  "Never promise things, never argue, never share personal info. For criticism, be gracious and brief. "
                  f"If someone asks for the full episode, point them to: {(src.credit_text if src else '') or 'the link in bio'}"
                  f"{(' ' + src.credit_link) if src and src.credit_link else ''}.")
        try:
            out = llm.structured(TriageBatch, system, f"Clip: {context}\n\nComments:\n{listing}",
                                 max_tokens=2200, temperature=0.4)
            results = {t.n - 1: t for t in out.items if 1 <= t.n <= len(comments)}
        except Exception as exc:  # noqa: BLE001
            log.warning("comment triage via LLM failed: %s", exc)
    for i, c in enumerate(comments):
        t = results.get(i)
        cat, attention = (t.category, t.needs_attention) if t else heuristic(c.text)
        if SPAM.search(c.text):
            cat = "spam"
        c.category = cat
        c.confidence = 0.8 if t else 0.5
        c.needs_attention = attention
        reply = (t.reply.strip() if t else "") or _template(cat, src)
        c.draft_reply = "" if cat in ("spam", "hate") else reply[:500]
        c.reply_status = "drafted" if c.draft_reply else "new"


@task("fetch_comments", lane="light")
def fetch_comments(ctx: JobContext, p: dict) -> None:
    from ..publish import instagram, youtube

    since = utcnow() - timedelta(days=14)
    with session() as s:
        accounts = s.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    waiting = 0
    for acc in accounts:
        with session() as s:
            posts = s.exec(select(Post).where(Post.account_id == acc.id, Post.status == "published",
                                              Post.published_at >= since)).all()
        for post in posts:
            try:
                raw = youtube.comments(acc, post.external_id) if acc.platform == "youtube" \
                    else instagram.comments(acc, post.external_id)
            except Exception as exc:  # noqa: BLE001
                log.info("comments for post %s: %s", post.id, exc)
                continue
            new: list[Comment] = []
            with session() as s:
                for r in raw:
                    if r.get("replied"):
                        continue
                    if s.exec(select(Comment).where(Comment.external_id == r["id"])).first():
                        continue
                    ts = None
                    if r.get("published_at"):
                        try:
                            ts = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00").replace("+0000", "+00:00"))
                        except ValueError:
                            ts = None
                    new.append(Comment(post_id=post.id, account_id=acc.id, platform=acc.platform, external_id=r["id"],
                                       author=r.get("author", ""), text=r.get("text", ""), published_at=ts,
                                       like_count=r.get("likes", 0)))
            if not new:
                continue
            with session() as s:
                clip = s.get(Clip, post.clip_id)
                src = s.get(Source, clip.source_id) if clip else None
            for i in range(0, len(new), 12):
                triage(new[i:i + 12], acc, src, post.title or post.caption[:200])
            funnels = list(acc.dm_funnels or [])
            if src is not None and getattr(src, "cta_keyword", ""):
                funnels.append({"keyword": src.cta_keyword, "message": src.credit_link or src.credit_text})
            for c in new:
                save(c)
                f = _funnel_match(c.text, funnels)
                if f and acc.platform == "instagram":
                    _run_funnel(acc, c, f)
                    continue
                if c.category in ("spam", "hate") and acc.auto_hide_spam:
                    try:
                        (youtube.hide if acc.platform == "youtube" else instagram.hide)(acc, c.external_id)
                        c.reply_status = "hidden"
                        save(c)
                    except Exception as exc:  # noqa: BLE001
                        log.info("couldn't hide comment %s: %s", c.id, exc)
                elif c.draft_reply and c.category in (acc.auto_reply_categories or []) and c.category in SAFE_AUTO \
                        and not c.needs_attention:
                    _post_reply(acc, c)
                elif c.reply_status == "drafted":
                    waiting += 1
    if waiting:
        events.emit("replies_waiting", f"{waiting} comment replies waiting for approval", level="info", count=waiting)


def _funnel_match(text: str, funnels: list[dict]) -> dict | None:
    words = set(re.findall(r"[\w']+", text.upper()))
    for f in funnels:
        kw = (f.get("keyword") or "").strip().upper()
        if kw and kw in words and f.get("message"):
            return f
    return None


def _run_funnel(acc: Account, c: Comment, f: dict) -> None:
    """Keyword comment -> DM with the link, plus a short public reply (helps the post's engagement too)."""
    from ..publish import instagram

    try:
        instagram.private_reply(acc, c.external_id, f["message"])
        c.reply_status, c.category = "dm_sent", "wants_link"
        public = f.get("public_reply") or "Sent you a DM 📩"
        try:
            c.reply_external_id = instagram.reply(acc, c.external_id, public)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        c.reply_status, c.error = "failed", f"DM failed: {exc}"[:500]
    save(c)


def _post_reply(acc: Account, c: Comment) -> bool:
    from ..publish import instagram, youtube

    try:
        rid = (youtube.reply if acc.platform == "youtube" else instagram.reply)(acc, c.external_id, c.draft_reply)
        c.reply_status, c.reply_external_id, c.error = "posted", rid, ""
        save(c)
        return True
    except Exception as exc:  # noqa: BLE001
        c.reply_status, c.error = "failed", str(exc)[:500]
        save(c)
        return False


@task("post_replies", lane="light")
def post_replies(ctx: JobContext, p: dict) -> None:
    with session() as s:
        approved = s.exec(select(Comment).where(Comment.reply_status == "approved")).all()
    for c in approved:
        with session() as s:
            acc = s.get(Account, c.account_id)
        if acc:
            _post_reply(acc, c)
