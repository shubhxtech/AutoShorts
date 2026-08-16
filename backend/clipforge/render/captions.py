"""Animated word-by-word captions.

Styles:
  clean    - mixed case, 2-3 words per beat, words appear as they're spoken, only stressed words
             go CAPS + brand colour (what most high-performing podcast clips do; the default)
  bold     - heavy uppercase, 2-3 words, active word in the brand colour (Hormozi-style)
  karaoke  - up to two lines, active word on a coloured box
  pop      - one or two huge words at a time with a springy pop-in
  boxed    - TikTok classic: active word in a white box, black text
  minimal  - sentence case with a soft shadow, for calm or educational pages
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import text as T
from .edl import OutWord

GREEN = (57, 255, 122, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def hex_rgba(h: str, a: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    if len(h) != 6:
        return (255, 212, 0, a)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


@dataclass
class CaptionStyle:
    name: str
    font: str
    size: float
    upper: bool
    max_words: int
    max_chars: int
    lines: int
    y: float                     # vertical centre as a fraction of frame height
    stroke: float
    shadow: float
    highlight: str               # color | box | none
    pop: float                   # pop-in overshoot (0 = none)
    active_scale: float = 1.0
    reveal: bool = False         # words appear one by one as they're spoken
    caps_emphasis: bool = False  # stressed words in CAPS
    bg: tuple | None = None      # rounded box behind each caption chunk


STYLES = {
    # Small word chips in a soft grey box (the "AI tools explainer" look).
    "chip": CaptionStyle("chip", "AvenirNext-DemiBold", 56, False, 2, 16, 1, 0.80, 0, 0, "none", 0.05,
                         bg=(58, 58, 58, 215)),
    "clean": CaptionStyle("clean", "AvenirNext-Heavy", 80, False, 3, 20, 1, 0.68, 8, 6, "emphasis", 0.06,
                          reveal=True, caps_emphasis=True),
    "bold": CaptionStyle("bold", "AvenirNext-Heavy", 88, True, 3, 18, 1, 0.66, 9, 6, "color", 0.18, 1.08),
    "karaoke": CaptionStyle("karaoke", "AvenirNext-Heavy", 72, False, 6, 26, 2, 0.70, 6, 5, "box", 0.08),
    "pop": CaptionStyle("pop", "Futura-CondensedExtraBold", 132, True, 2, 12, 1, 0.62, 11, 7, "color", 0.30, 1.0),
    "boxed": CaptionStyle("boxed", "AvenirNext-Heavy", 76, False, 4, 22, 2, 0.70, 0, 0, "box_white", 0.10),
    "minimal": CaptionStyle("minimal", "AvenirNext-DemiBold", 62, False, 8, 34, 2, 0.75, 0, 9, "none", 0.0),
}


@dataclass
class Chunk:
    words: list[int]            # indices into the OutWord list
    start: float
    end: float


WEAK_END = {"the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "my", "your", "our",
            "his", "her", "their", "is", "was", "so", "if", "as", "by", "from"}


def chunk_words(words: list[OutWord], style: CaptionStyle) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[int] = []
    chars = 0
    limit = style.max_words * style.lines
    for i, w in enumerate(words):
        gap = w.start - words[i - 1].end if i else 0
        n = len(w.text)
        full = len(cur) >= limit or chars + n > style.max_chars * style.lines
        if cur and (full or gap > 0.35 or re.search(r"[.!?…।]$", words[cur[-1]].text)
                    or (len(cur) >= 2 and words[cur[-1]].text.endswith((",", ";", ":")))):
            carry: list[int] = []
            # Don't strand "the"/"and"/"to" at the end of a chunk; move it to the next one.
            if full and len(cur) > 1 and re.sub(r"[^\w']", "", words[cur[-1]].text.lower()) in WEAK_END:
                carry = [cur.pop()]
            chunks.append(Chunk(cur, words[cur[0]].start, words[cur[-1]].end))
            cur = carry
            chars = sum(len(words[j].text) + 1 for j in cur)
        cur.append(i)
        chars += n + 1
    if cur:
        chunks.append(Chunk(cur, words[cur[0]].start, words[cur[-1]].end))
    for a, b in zip(chunks, chunks[1:]):
        a.end = min(max(a.end + 0.25, a.end), b.start)
    if chunks:
        chunks[-1].end += 0.4
    return chunks


class CaptionTrack:
    def __init__(self, words: list[OutWord], style_name: str, brand: str, emphasis: set[int],
                 frame_w: int = 1080, frame_h: int = 1920, font_override: str = "", edits: dict | None = None,
                 y_override: float | None = None, size_scale: float = 1.0):
        self.style = STYLES.get(style_name, STYLES["bold"])
        self.words = words
        self.brand = hex_rgba(brand)
        self.emphasis = emphasis
        self.W, self.H = frame_w, frame_h
        font = T.resolve_font(font_override, self.style.font, "AvenirNext-Heavy", "Arial-Black")
        self.tstyle = T.TextStyle(font=font, size=self.style.size * size_scale, stroke=self.style.stroke * size_scale,
                                  shadow=self.style.shadow, box_pad=(16, 4), box_radius=16,
                                  background=self.style.bg, background_pad=(22, 10), background_radius=16)
        self.edits = {int(k): v for k, v in (edits or {}).items()}
        self.y = y_override if y_override is not None else self.style.y
        self.chunks = chunk_words(words, self.style)
        self._cache: dict[tuple, np.ndarray] = {}
        self.safe_w = frame_w - 2 * 90 - 60      # keep clear of side UI (Reels buttons on the right)

    def _word_text(self, i: int) -> str:
        w = self.edits.get(self.words[i].index, self.words[i].text)
        w = w.strip()
        if self.style.upper or (self.style.caps_emphasis and i in self.emphasis):
            w = w.upper()
        if self.style.name in ("bold", "pop", "clean"):
            w = w.rstrip(",.;:")
        return w

    def _sprite(self, ci: int, active: int | None) -> np.ndarray:
        key = (ci, active)
        if key in self._cache:
            return self._cache[key]
        ch = self.chunks[ci]
        texts = [self._word_text(i) for i in ch.words]
        style = self.tstyle
        # Wrap into at most `lines` lines; shrink the font if it still doesn't fit.
        scale = 1.0
        for _ in range(6):
            st = T.TextStyle(**{**style.__dict__, "size": style.size * scale})
            lines = T.wrap(texts, st, self.safe_w)
            if len(lines) <= self.style.lines and all(
                    T.measure(" ".join(texts[j] for j in ln), st) <= self.safe_w for ln in lines):
                break
            scale *= 0.88
        spans: list[list[T.Span]] = []
        for ln in lines:
            row: list[T.Span] = []
            for k, j in enumerate(ln):
                i = ch.words[j]
                is_active = i == active
                is_emph = i in self.emphasis
                color, box, sc = WHITE, None, 1.0
                hl = self.style.highlight
                if is_emph:
                    color = GREEN if hl in ("color",) and self.brand[:3] != GREEN[:3] else self.brand
                    sc = 1.12 if hl == "emphasis" else 1.1
                if is_active:
                    if hl == "color":
                        color = self.brand if not is_emph else GREEN
                        sc = max(sc, self.style.active_scale)
                    elif hl == "box":
                        box = self.brand
                        color = BLACK if sum(self.brand[:3]) > 500 else WHITE
                    elif hl == "box_white":
                        box, color = WHITE, BLACK
                if self.style.reveal and active is not None and i > active:
                    color = (color[0], color[1], color[2], 0)   # not spoken yet: keep the layout, hide the word
                row.append(T.Span(texts[j] + (" " if k < len(ln) - 1 else ""), color, sc, box))
            spans.append(row)
        sprite = T.render(spans, st)
        self._cache[key] = sprite
        if len(self._cache) > 400:
            self._cache.pop(next(iter(self._cache)))
        return sprite

    def chunk_at(self, t: float) -> int | None:
        for i, ch in enumerate(self.chunks):
            if ch.start - 0.05 <= t < ch.end:
                return i
        return None

    def draw(self, frame: np.ndarray, t: float, y_center: float | None = None) -> None:
        ci = self.chunk_at(t)
        if ci is None:
            return
        ch = self.chunks[ci]
        active = None
        for i in ch.words:
            if self.words[i].start - 0.04 <= t:
                active = i
        needs_active = self.style.highlight != "none" or self.style.reveal
        sprite = self._sprite(ci, active if needs_active else None)
        age = t - ch.start
        if self.style.pop and age < 0.16:
            k = max(0.0, age / 0.16)
            s = 0.78 + (1 + self.style.pop - 0.78) * k if k < 0.6 else 1 + self.style.pop * (1 - (k - 0.6) / 0.4)
            sprite = T.scale_sprite(sprite, max(0.6, s))
        h, w = sprite.shape[:2]
        yc = (y_center if y_center is not None else self.y) * self.H
        x = (self.W - w) // 2 - 20         # nudge left of the Reels action buttons
        T.blend(frame, sprite, int(x), int(yc - h / 2))
