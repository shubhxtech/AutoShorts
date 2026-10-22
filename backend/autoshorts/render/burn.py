"""Burn the editable text layer (captions, hook, headline, emoji, CTA, progress bar) onto a base clip.

The base render already contains framing, zooms, b-roll, panels and final audio. Editing captions or
text in the dashboard only changes the overlay spec, so the final video is made in one fast pass
(decode -> draw text -> encode, audio copied) instead of a full re-render.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .. import logs
from ..media import ffmpeg_bin, video_encoder_args
from .captions import CaptionTrack
from .edl import OutWord
from .overlays import H, W, Overlays
from .plan import CardEvent, EmojiPop

log = logs.get("burn")

# Overrides the dashboard can change without re-rendering the base.
LIVE_KEYS = {"caption_style", "caption_y", "caption_scale", "hook", "headline", "cta", "progress_bar",
             "emoji", "end_card", "brand", "caption_edits", "hide_captions"}


def merged(spec: dict, overrides: dict | None) -> dict:
    out = dict(spec)
    for k, v in (overrides or {}).items():
        if k in LIVE_KEYS and v is not None:
            out[k] = v
    return out


def build(spec: dict, words: list, overrides: dict | None = None) -> tuple[CaptionTrack, Overlays, dict]:
    s = merged(spec, overrides)
    ow = [OutWord(index=int(w[0]), text=w[1], start=float(w[2]), end=float(w[3]), src_start=float(w[2]),
                  prob=float(w[4]) if len(w) > 4 else 1.0) for w in words]
    caps = CaptionTrack(ow, s.get("caption_style") or "clean", s.get("brand") or "#FFD400", set(s.get("emphasis") or []),
                        font_override=s.get("caption_font") or "", edits=s.get("caption_edits") or {},
                        size_scale=float(s.get("caption_scale") or 1.0))
    emoji = [EmojiPop(float(t), e) for t, e in (s.get("emoji") or [])] if s.get("emoji") is not False else []
    panels = [CardEvent(float(a), float(b), "", panel=True) for a, b in (s.get("panels") or [])]
    over = Overlays(hook=s.get("hook") or "", headline=s.get("headline") or "", credit=s.get("credit") or "",
                    credit_link=s.get("credit_link") or "", cta=s.get("cta") or "", brand=s.get("brand") or "#FFD400",
                    duration=float(s["duration"]), hook_seconds=float(s.get("hook_seconds") or 0),
                    progress_bar=bool(s.get("progress_bar")), end_card=bool(s.get("end_card")),
                    cards=panels, emoji=emoji, flashes=[], cta_keyword=s.get("cta_keyword") or "",
                    key_words=set(s.get("key_words") or []))
    return caps, over, s


def caption_y_at(s: dict, t: float) -> float:
    if s.get("caption_y") is not None:
        return float(s["caption_y"])
    track = s.get("caption_y_track") or []
    if not track:
        return 0.68
    lo, hi = 0, len(track) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return float(track[lo][1])


def burn(base: Path, spec: dict, words: list, overrides: dict | None, out: Path,
         progress: Callable[[float, str], None] | None = None) -> Path:
    caps, over, s = build(spec, words, overrides)
    fps = float(s.get("fps") or 30)
    n = int(round(float(s["duration"]) * fps))
    dec = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(base), "-an", "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-i", str(base), "-map", "0:v", "-map", "1:a",
                            *video_encoder_args("auto", "10M"), "-pix_fmt", "yuv420p", "-r", f"{fps}", "-c:a", "copy",
                            "-movflags", "+faststart", "-shortest", str(out)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    size = W * H * 3
    i = 0
    try:
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            t = i / fps
            y = caption_y_at(s, t)
            if not s.get("hide_captions"):
                caps.draw(frame, t, y)
            over.draw(frame, t, y, layer="text")
            enc.stdin.write(frame.tobytes())
            i += 1
            if progress and i % 30 == 0:
                progress(i / max(1, n), f"adding text {i}/{n} frames")
    finally:
        dec.stdout.close()
        dec.wait()
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"burn failed: {err[-800:]}")
    log.info("burned text onto %s (%d frames)", out.name, i)
    return out
