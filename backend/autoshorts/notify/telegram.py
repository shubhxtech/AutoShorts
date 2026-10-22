"""Telegram bot: pushes alerts and answers a few commands (/status, /pause, /resume)."""

from __future__ import annotations

import html
import threading

import httpx

from .. import logs, settings

log = logs.get("telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def escape(s: str) -> str:
    return html.escape(s or "", quote=False)


def configured() -> bool:
    return bool(settings.get("telegram_bot_token")) and bool(settings.get("telegram_chat_id"))


def _call(method: str, http_timeout: float = 15, **params):
    token = settings.get("telegram_bot_token")
    if not token:
        return None
    r = httpx.post(API.format(token=token, method=method), json=params, timeout=http_timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"telegram {method} failed")
    return data.get("result")


def send(text: str, chat_id: str | None = None) -> bool:
    chat = chat_id or settings.get("telegram_chat_id")
    if not chat:
        return False
    try:
        _call("sendMessage", chat_id=chat, text=text[:4000], parse_mode="HTML", disable_web_page_preview=True)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram send failed: %s", exc)
        return False


def _status_text() -> str:
    from ..api.summary import today_summary

    s = today_summary()
    return (
        f"<b>AutoShorts</b> {'⏸ paused' if settings.get('automation_paused') else '▶️ running'}\n"
        f"Waiting for review: <b>{s['review']}</b>\n"
        f"Scheduled posts: <b>{s['scheduled']}</b>\n"
        f"Published today: <b>{s['published_today']}</b>\n"
        f"Views (24h): <b>{s['views_24h']:,}</b>\n"
        f"Replies waiting: <b>{s['replies_waiting']}</b>"
    )


def _handle(msg: dict) -> None:
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip().split("@")[0]
    known = settings.get("telegram_chat_id")
    if text == "/start" and not known:
        settings.set("telegram_chat_id", chat_id)
        send("Connected. You'll get AutoShorts alerts here. Try /status.", chat_id)
        return
    if known and chat_id != str(known):
        return  # ignore strangers
    if text == "/status":
        send(_status_text(), chat_id)
    elif text == "/pause":
        settings.set("automation_paused", True)
        send("⏸ Automation paused. Nothing will be ingested or posted until /resume.", chat_id)
    elif text == "/resume":
        settings.set("automation_paused", False)
        send("▶️ Automation resumed.", chat_id)
    elif text in ("/help", "/start"):
        send("/status – today's numbers\n/pause – stop ingesting and posting\n/resume – start again", chat_id)


def _poll(stop: threading.Event) -> None:
    offset = 0
    while not stop.is_set():
        if not settings.get("telegram_bot_token"):
            stop.wait(30)
            continue
        try:
            updates = _call("getUpdates", http_timeout=40, offset=offset, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.debug("telegram poll: %s", exc)
            stop.wait(15)
            continue
        for upd in updates or []:
            offset = max(offset, upd["update_id"] + 1)
            if "message" in upd:
                try:
                    _handle(upd["message"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("telegram command failed: %s", exc)


def start_polling(stop: threading.Event) -> None:
    threading.Thread(target=_poll, args=(stop,), name="telegram", daemon=True).start()
