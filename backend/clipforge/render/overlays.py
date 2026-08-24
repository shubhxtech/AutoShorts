"""On-screen graphics drawn over the reframed video."""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from . import text as T
from .captions import hex_rgba
from .plan import CardEvent, EmojiPop

W, H = 1080, 1920
SAFE_TOP = 150          # platform status bar / top UI
SAFE_BOTTOM = 1920 - 360


def _ease_out(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def _back_out(x: float, s: float = 1.6) -> float:
    x = max(0.0, min(1.0, x)) - 1
    return x * x * ((s + 1) * x + s) + 1


class Overlays:
    def __init__(self, *, hook: str, headline: str, credit: str, credit_link: str, cta: str, brand: str,
                 duration: float, hook_seconds: float, progress_bar: bool, end_card: bool,
                 cards: list[CardEvent], emoji: list[EmojiPop], flashes: list[float], layout_hint: str = "follow",
                 cta_keyword: str = "", key_words: set[str] | None = None):
        self.cta_keyword = cta_keyword.strip()
        self.key_words = {w.lower() for w in (key_words or set())}
        self.hook, self.headline, self.credit, self.link, self.cta = hook, headline, credit, credit_link, cta
        self.brand = hex_rgba(brand)
        self.duration, self.hook_s = duration, hook_seconds
        self.progress_bar, self.end_card = progress_bar, end_card
        self.cards, self.emoji, self.flashes = cards, emoji, flashes
        self.layout_hint = layout_hint
        font = T.resolve_font("AvenirNext-Heavy", "Arial-Black")
        self.font = font
        self._hook_sprite = self._make_hook(hook) if hook else None
        self._head_sprite = self._make_headline(headline or hook) if (headline or hook) else None
        self._credit_sprite = self._make_small(f"🎙 {credit}") if credit else None
        self._end_sprites = self._make_end() if end_card else None

    # ------------------------------------------------------------ sprites

    def _wrap_lines(self, text: str, style: T.TextStyle, max_w: float, max_lines: int = 3) -> list[str]:
        words = text.split()
        if not words:
            return []
        idx = T.wrap(words, style, max_w)
        return [" ".join(words[i] for i in ln) for ln in idx][:max_lines]

    def _make_hook(self, text: str) -> np.ndarray:
        text = text.strip().rstrip(".")
        # Big enough to sell the clip on frame 0; sized by length (mirrored in the dashboard preview).
        style = T.TextStyle(font=self.font, size=hook_size(text), background=(255, 255, 255, 255), background_pad=(40, 24),
                            background_radius=28, shadow=0, line_spacing=1.04)
        lines = self._wrap_lines(text, style, 900)
        while len(lines) > 2 and style.size > 48:
            style.size -= 6
            lines = self._wrap_lines(text, style, 900)
        key = _key_word(text, self.key_words)
        rows = []
        for l in lines:
            words = l.split(" ")
            row = []
            for j, w in enumerate(words):
                sp = w + (" " if j < len(words) - 1 else "")
                if key and w.strip(",.!?").lower() == key:
                    row.append(T.Span(sp, (12, 12, 12, 255), 1.0, self.brand))
                else:
                    row.append(T.Span(sp, (12, 12, 12, 255)))
            rows.append(row)
        return T.render(rows, style)

    def _make_headline(self, text: str) -> np.ndarray:
        style = T.TextStyle(font=self.font, size=46, background=(0, 0, 0, 170), background_pad=(26, 14),
                            background_radius=20, line_spacing=1.0)
        lines = self._wrap_lines(text, style, 860, 2)
        return T.render([[T.Span(l, (255, 255, 255, 255))] for l in lines], style)

    def _make_small(self, text: str) -> np.ndarray:
        style = T.TextStyle(font=T.resolve_font("AvenirNext-DemiBold", self.font), size=34, shadow=6,
                            shadow_color=(0, 0, 0, 200))
        return T.render([[T.Span(text, (255, 255, 255, 235))]], style)

    def _make_end(self) -> list[np.ndarray]:
        out = []
        if self.cta:
            st = T.TextStyle(font=self.font, size=58, background=self.brand, background_pad=(34, 18),
                             background_radius=24)
            color = (0, 0, 0, 255) if sum(self.brand[:3]) > 500 else (255, 255, 255, 255)
            out.append(T.render([[T.Span(self.cta, color)]], st))
        if self.credit:
            st = T.TextStyle(font=T.resolve_font("AvenirNext-DemiBold", self.font), size=38,
                             background=(0, 0, 0, 190), background_pad=(26, 14), background_radius=18)
            lines = [[T.Span(f"🎙 Full episode: {self.credit}", (255, 255, 255, 255))]]
            if self.link:
                lines.append([T.Span(self.link, (200, 200, 200, 255), 0.8)])
            out.append(T.render(lines, st))
        return out

    @lru_cache(maxsize=16)
    def _card_sprite(self, text: str, emoji: str) -> np.ndarray:
        style = T.TextStyle(font=T.resolve_font("Futura-CondensedExtraBold", self.font), size=150, stroke=0,
                            shadow=14, shadow_color=(0, 0, 0, 220), line_spacing=0.95)
        lines = self._wrap_lines(text.upper(), style, 900, 3)
        spans = [[T.Span(l, (255, 255, 255, 255))] for l in lines]
        if emoji:
            spans.insert(0, [T.Span(emoji, (255, 255, 255, 255), 0.8)])
        return T.render(spans, style)

    @lru_cache(maxsize=32)
    def _emoji_sprite(self, e: str) -> np.ndarray:
        return T.render([[T.Span(e)]], T.TextStyle(font=self.font, size=150))

    # ------------------------------------------------------------ drawing

    def panel_at(self, t: float) -> CardEvent | None:
        return next((c for c in self.cards if c.panel and c.start <= t <= c.end), None)

    def draw_card_background(self, frame: np.ndarray, t: float) -> bool:
        """Context cards dim and blur the video behind them. Returns True while a card is up."""
        for c in self.cards:
            if c.start <= t <= c.end and not c.panel:
                k = min(_ease_out((t - c.start) / 0.15), _ease_out((c.end - t) / 0.15))
                small = cv2.resize(frame, (W // 8, H // 8), interpolation=cv2.INTER_AREA)
                small = cv2.GaussianBlur(small, (0, 0), 3)
                blurred = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
                dark = (blurred.astype(np.float32) * 0.45).astype(np.uint8)
                cv2.addWeighted(dark, k, frame, 1 - k, 0, dst=frame)
                return True
        return False

    def draw(self, frame: np.ndarray, t: float, caption_y: float, layer: str = "all") -> None:
        """layer: "base" = layout graphics baked into the clip (cards, flashes);
        "text" = editable text drawn live in the preview and burned on approval; "all" = both."""
        split = abs(caption_y - 0.5) < 0.05
        card_y = 0.27 if split else 0.44
        hook_y = 0.125 if split else 0.21
        d = self.duration
        if layer in ("all", "text"):
            self._draw_text(frame, t, caption_y, hook_y, d)
        if layer in ("all", "base"):
            self._draw_base(frame, t, card_y)

    def _draw_text(self, frame: np.ndarray, t: float, caption_y: float, hook_y: float, d: float) -> None:
        # Hook card: pops in, holds, then shrinks into the persistent headline.
        if self._hook_sprite is not None and t < self.hook_s:
            # Frame 0 must already sell the clip: the hook is fully readable from the first frame.
            k = _back_out(t / 0.18)
            out = 1.0 - _ease_out((t - (self.hook_s - 0.25)) / 0.25)
            spr = T.scale_sprite(self._hook_sprite, 0.94 + 0.06 * k)
            h, w = spr.shape[:2]
            T.blend(frame, spr, (W - w) // 2, int(max(SAFE_TOP, H * hook_y - h / 2)), opacity=max(0.0, out))
            if self._credit_sprite is not None:
                ch, cw = self._credit_sprite.shape[:2]
                T.blend(frame, self._credit_sprite, (W - cw) // 2, int(max(SAFE_TOP, H * hook_y - h / 2) + h + 12),
                        opacity=max(0.0, out))
        elif self._head_sprite is not None and t < d - (1.9 if self.end_card else 0) and self.panel_at(t) is None:
            k = _ease_out((t - self.hook_s) / 0.3)
            h, w = self._head_sprite.shape[:2]
            T.blend(frame, self._head_sprite, (W - w) // 2, SAFE_TOP + 10, opacity=k)

        for e in self.emoji:
            age = t - e.start
            if 0 <= age <= 0.9:
                spr = self._emoji_sprite(e.emoji)
                s = 0.4 + 0.75 * _back_out(age / 0.25)
                fade = 1.0 - _ease_out((age - 0.7) / 0.2)
                spr = T.scale_sprite(spr, s)
                h, w = spr.shape[:2]
                T.blend(frame, spr, (W - w) // 2, int(caption_y * H - 120 - h - 20 * age), opacity=fade)

        if self.cta_keyword and t >= d - 2.4:
            from .graphics import draw_cta

            draw_cta(frame, self.cta_keyword, t - (d - 2.4))
        elif self._end_sprites and t >= d - 1.9:
            k = _back_out((t - (d - 1.9)) / 0.3)
            y = int(H * 0.30)
            for spr in self._end_sprites:
                s = T.scale_sprite(spr, 0.8 + 0.2 * k)
                h, w = s.shape[:2]
                T.blend(frame, s, (W - w) // 2, y, opacity=min(1.0, k))
                y += h + 18

        if self.progress_bar and d > 0:
            frac = min(1.0, t / d)
            frame[0:10, :] = (frame[0:10, :].astype(np.float32) * 0.45).astype(np.uint8)
            b, g, r = self.brand[2], self.brand[1], self.brand[0]
            frame[0:10, : int(W * frac)] = (b, g, r)

    def _draw_base(self, frame: np.ndarray, t: float, card_y: float) -> None:
        for c in self.cards:
            if c.start <= t <= c.end and not c.panel:
                spr = self._card_sprite(c.text, c.emoji)
                k = _back_out((t - c.start) / 0.25)
                fade = _ease_out((c.end - t) / 0.15)
                spr = T.scale_sprite(spr, 0.6 + 0.4 * k)
                h, w = spr.shape[:2]
                T.blend(frame, spr, (W - w) // 2, int(H * card_y - h / 2), opacity=fade)

        for f in self.flashes:
            age = t - f
            if -0.03 <= age <= 0.09:
                # A quick, subtle pulse: a full white-out reads as a glitch on phones.
                k = max(0.0, 1.0 - abs(age) / 0.09) * 0.32
                white = np.full_like(frame, 255)
                cv2.addWeighted(white, k, frame, 1 - k, 0, dst=frame)


def hook_size(text: str) -> int:
    n = len(text)
    return 84 if n <= 26 else 76 if n <= 40 else 66 if n <= 56 else 58


_HOOK_STOP = {"the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "is", "are", "was",
              "you", "your", "this", "that", "it", "i", "my", "we", "they", "he", "she", "be", "do", "does", "why", "how",
              "what", "who", "when", "will", "can", "never", "just", "about", "from", "most", "people"}


def _key_word(text: str, prefer: set[str] | None = None) -> str:
    """The word in a hook worth highlighting: numbers, then words the LLM marked as key, then the longest content word."""
    words = [w.strip(",.!?:;\"'").lower() for w in text.split()]
    nums = [w for w in words if any(ch.isdigit() for ch in w)]
    if nums:
        return nums[0]
    for w in words:
        if prefer and w in prefer and w not in _HOOK_STOP:
            return w
    content = [w for w in words if w and w not in _HOOK_STOP]
    return max(content, key=len) if content else ""


def fit_frame(src: np.ndarray, out_w: int = W, out_h: int = H, y_center: float = 0.46) -> np.ndarray:
    """Full source frame over a blurred, zoomed copy of itself."""
    sh, sw = src.shape[:2]
    scale_bg = max(out_w / sw, out_h / sh)
    small = cv2.resize(src, (max(1, int(sw * scale_bg / 10)), max(1, int(sh * scale_bg / 10))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 4)
    bg = cv2.resize(small, (int(sw * scale_bg), int(sh * scale_bg)), interpolation=cv2.INTER_LINEAR)
    by, bx = (bg.shape[0] - out_h) // 2, (bg.shape[1] - out_w) // 2
    out = (bg[by:by + out_h, bx:bx + out_w].astype(np.float32) * 0.55).astype(np.uint8)
    fg_w = out_w
    fg_h = int(sh * fg_w / sw)
    if fg_h > out_h * 0.7:
        fg_h = int(out_h * 0.7)
        fg_w = int(sw * fg_h / sh)
    fg = cv2.resize(src, (fg_w, fg_h), interpolation=cv2.INTER_AREA if fg_w < sw else cv2.INTER_CUBIC)
    y0 = int(out_h * y_center - fg_h / 2)
    x0 = (out_w - fg_w) // 2
    out[y0:y0 + fg_h, x0:x0 + fg_w] = fg
    return out


def audiogram_frame(art: np.ndarray | None, level: float, t: float, bars: np.ndarray) -> np.ndarray:
    """For audio-only podcast sources: artwork card plus a live waveform."""
    out = np.zeros((H, W, 3), np.uint8)
    if art is not None:
        out[:] = fit_frame(art)
    else:
        out[:] = (24, 20, 30)
    n = len(bars)
    bw = 18
    gap = 10
    total = n * bw + (n - 1) * gap
    x0 = (W - total) // 2
    yc = int(H * 0.80)
    for i, v in enumerate(bars):
        h = int(12 + 160 * float(v))
        x = x0 + i * (bw + gap)
        cv2.rectangle(out, (x, yc - h // 2), (x + bw, yc + h // 2), (255, 255, 255), -1, lineType=cv2.LINE_AA)
    return out
