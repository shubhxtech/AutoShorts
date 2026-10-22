"""Text rasterizer for captions and overlays.

On macOS this uses CoreText, which shapes every script correctly (Devanagari,
Arabic, CJK) and renders Apple Color Emoji, falling back across fonts
automatically. Elsewhere it uses Pillow. Output is a premultiplied RGBA uint8
array, ready for `blend`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

RGBA = tuple[int, int, int, int]


@dataclass
class Span:
    text: str
    color: RGBA = (255, 255, 255, 255)
    scale: float = 1.0                    # relative font size for this span
    box: RGBA | None = None               # highlight box behind the span


@dataclass
class TextStyle:
    font: str = "AvenirNext-Heavy"
    size: float = 80
    stroke: float = 0                     # outline width in px
    stroke_color: RGBA = (0, 0, 0, 255)
    shadow: float = 0                     # blur radius in px
    shadow_color: RGBA = (0, 0, 0, 170)
    shadow_offset: tuple[float, float] = (0, -4)
    line_spacing: float = 1.05
    box_pad: tuple[float, float] = (14, 6)
    box_radius: float = 14
    background: RGBA | None = None        # rounded box behind the whole block
    background_pad: tuple[float, float] = (34, 22)
    background_radius: float = 28
    extra: dict = field(default_factory=dict)


def blend(dst: np.ndarray, src: np.ndarray, x: int, y: int, opacity: float = 1.0) -> None:
    """Composite premultiplied RGBA `src` onto BGR `dst` at (x, y), in place, clipped to bounds."""
    h, w = src.shape[:2]
    H, W = dst.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0 or opacity <= 0:
        return
    s = src[y0 - y:y1 - y, x0 - x:x1 - x]
    a = s[..., 3:4].astype(np.float32) * (opacity / 255.0)
    rgb = s[..., 2::-1].astype(np.float32) * opacity      # RGBA -> BGR
    region = dst[y0:y1, x0:x1].astype(np.float32)
    dst[y0:y1, x0:x1] = np.clip(rgb + region * (1.0 - a), 0, 255).astype(np.uint8)


def scale_sprite(img: np.ndarray, s: float) -> np.ndarray:
    import cv2

    if abs(s - 1.0) < 1e-3:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------- CoreText backend

_MAC = sys.platform == "darwin"


def _backend() -> str:
    """coretext on macOS, skia elsewhere (AUTOSHORTS_TEXT=skia|pillow forces one), Pillow as a last resort."""
    import os

    forced = os.environ.get("AUTOSHORTS_TEXT", "").lower()
    if forced in ("skia", "pillow"):
        return forced
    if _have_coretext():
        return "coretext"
    from . import text_skia

    return "skia" if text_skia.available() else "pillow"


def _have_coretext() -> bool:
    import os

    if not _MAC or os.environ.get("AUTOSHORTS_TEXT", "").lower() in ("skia", "pillow"):
        return False
    try:
        import CoreText  # noqa: F401
        import Quartz  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=256)
def _ct_font(name: str, size: float):
    import CoreText

    return CoreText.CTFontCreateWithName(name, size, None)


def _cg_color(c: RGBA):
    import Quartz

    return Quartz.CGColorCreateSRGB(c[0] / 255, c[1] / 255, c[2] / 255, c[3] / 255)


def _ct_line(spans: list[Span], style: TextStyle, stroke_pass: bool):
    import CoreText
    from Foundation import NSMakeRange, NSMutableAttributedString

    full = "".join(sp.text for sp in spans)
    attr = NSMutableAttributedString.alloc().initWithString_(full)
    pos = 0
    for sp in spans:
        n = len(sp.text.encode("utf-16-le")) // 2
        rng = NSMakeRange(pos, n)
        attr.addAttribute_value_range_(CoreText.kCTFontAttributeName, _ct_font(style.font, style.size * sp.scale), rng)
        if stroke_pass:
            width_pct = 100.0 * style.stroke * 2 / (style.size * sp.scale)
            sc = style.stroke_color if sp.color[3] > 0 else (0, 0, 0, 0)
            attr.addAttribute_value_range_(CoreText.kCTStrokeWidthAttributeName, width_pct, rng)
            attr.addAttribute_value_range_(CoreText.kCTStrokeColorAttributeName, _cg_color(sc), rng)
            attr.addAttribute_value_range_(CoreText.kCTForegroundColorAttributeName, _cg_color((0, 0, 0, 0)), rng)
        else:
            attr.addAttribute_value_range_(CoreText.kCTForegroundColorAttributeName, _cg_color(sp.color), rng)
        pos += n
    return CoreText.CTLineCreateWithAttributedString(attr)


def _ct_metrics(line) -> tuple[float, float, float]:
    import CoreText

    width, ascent, descent, _ = CoreText.CTLineGetTypographicBounds(line, None, None, None)
    return float(width), float(ascent), float(descent)


def _span_offsets(line, spans: list[Span]) -> list[tuple[float, float]]:
    """x-extent of each span within a CTLine, for highlight boxes."""
    import CoreText

    out, pos = [], 0
    for sp in spans:
        n = len(sp.text.encode("utf-16-le")) // 2
        a = CoreText.CTLineGetOffsetForStringIndex(line, pos, None)[0]
        b = CoreText.CTLineGetOffsetForStringIndex(line, pos + n, None)[0]
        out.append((float(a), float(b)))
        pos += n
    return out


def _render_coretext(lines: list[list[Span]], style: TextStyle) -> np.ndarray:
    import CoreText
    import Quartz

    fill_lines = [_ct_line(sp, style, False) for sp in lines]
    stroke_lines = [_ct_line(sp, style, True) for sp in lines] if style.stroke > 0 else None
    metrics = [_ct_metrics(l) for l in fill_lines]
    pad = int(style.stroke * 2 + style.shadow * 2 + 8)
    bg_px, bg_py = style.background_pad if style.background else (0, 0)
    line_h = [max(m[1] + m[2], style.size * 0.9) * style.line_spacing for m in metrics]
    text_w = max(m[0] for m in metrics) if metrics else 1
    W = int(text_w + 2 * pad + 2 * bg_px) + 2
    H = int(sum(line_h) + 2 * pad + 2 * bg_py) + 2
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(None, W, H, 8, W * 4, cs, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetShouldAntialias(ctx, True)
    Quartz.CGContextSetShouldSmoothFonts(ctx, False)

    if style.background:
        _ct_round_rect(ctx, pad, pad, W - 2 * pad, H - 2 * pad, style.background_radius, style.background)

    # Baselines from the top (CG origin is bottom-left).
    y_top = H - pad - bg_py
    baselines = []
    for (w, asc, desc), lh in zip(metrics, line_h):
        extra = (lh - (asc + desc)) / 2
        baselines.append(y_top - extra - asc)
        y_top -= lh

    for li, spans in enumerate(lines):
        w, asc, desc = metrics[li]
        x = (W - w) / 2
        if any(sp.box for sp in spans):
            for (a, b), sp in zip(_span_offsets(fill_lines[li], spans), spans):
                if sp.box:
                    bx, by = style.box_pad
                    _ct_round_rect(ctx, x + a - bx, baselines[li] - desc - by, (b - a) + 2 * bx,
                                   asc + desc + 2 * by, style.box_radius, sp.box)

    if style.shadow > 0:
        Quartz.CGContextSetShadowWithColor(ctx, style.shadow_offset, style.shadow, _cg_color(style.shadow_color))
    if stroke_lines:
        Quartz.CGContextSetLineJoin(ctx, Quartz.kCGLineJoinRound)
        for li, line in enumerate(stroke_lines):
            Quartz.CGContextSetTextPosition(ctx, (W - metrics[li][0]) / 2, baselines[li])
            CoreText.CTLineDraw(line, ctx)
        Quartz.CGContextSetShadowWithColor(ctx, (0, 0), 0, None)
    for li, line in enumerate(fill_lines):
        Quartz.CGContextSetTextPosition(ctx, (W - metrics[li][0]) / 2, baselines[li])
        CoreText.CTLineDraw(line, ctx)

    img = Quartz.CGBitmapContextCreateImage(ctx)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    arr = np.frombuffer(bytes(data), dtype=np.uint8).reshape(H, bpr // 4, 4)[:, :W]
    return np.ascontiguousarray(arr)


def _ct_round_rect(ctx, x, y, w, h, r, color: RGBA):
    import Quartz

    r = min(r, w / 2, h / 2)
    path = Quartz.CGPathCreateWithRoundedRect(Quartz.CGRectMake(x, y, w, h), r, r, None)
    Quartz.CGContextAddPath(ctx, path)
    Quartz.CGContextSetFillColorWithColor(ctx, _cg_color(color))
    Quartz.CGContextFillPath(ctx)


def ct_measure(text: str, font: str, size: float) -> float:
    return _ct_metrics(_ct_line([Span(text)], TextStyle(font=font, size=size), False))[0]


@lru_cache(maxsize=64)
def font_exists(name: str) -> bool:
    if not _have_coretext():
        return False
    import CoreText

    f = _ct_font(name, 20)
    return CoreText.CTFontCopyPostScriptName(f) == name


def resolve_font(*names: str) -> str:
    if _backend() == "skia":
        from .text_skia import FONT_MAP

        # Every Mac font the editor uses has an open look-alike, so keep the first (most specific) choice.
        return next((n for n in names if n in FONT_MAP), next((n for n in names if n), names[-1]))
    for n in names:
        if n and font_exists(n):
            return n
    return names[-1]


# ---------------------------------------------------------------- Pillow backend

_PIL_FALLBACKS = ["/System/Library/Fonts/Supplemental/Arial Black.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]


@lru_cache(maxsize=64)
def _pil_font(size: int):
    from PIL import ImageFont

    for p in _PIL_FALLBACKS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_pillow(lines: list[list[Span]], style: TextStyle) -> np.ndarray:
    from PIL import Image, ImageDraw

    pad = int(style.stroke * 2 + style.shadow * 2 + 8)
    bg_px, bg_py = style.background_pad if style.background else (0, 0)
    measured = []
    for spans in lines:
        parts = []
        for sp in spans:
            f = _pil_font(int(style.size * sp.scale))
            parts.append((sp, f, f.getlength(sp.text)))
        measured.append(parts)
    line_w = [sum(p[2] for p in parts) for parts in measured]
    lh = style.size * 1.2 * style.line_spacing
    W = int(max(line_w + [1]) + 2 * pad + 2 * bg_px) + 2
    H = int(lh * len(lines) + 2 * pad + 2 * bg_py) + 2
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if style.background:
        d.rounded_rectangle([pad, pad, W - pad, H - pad], radius=int(style.background_radius), fill=style.background)
    y = pad + bg_py
    for parts, lw in zip(measured, line_w):
        x = (W - lw) / 2
        for sp, f, w in parts:
            if sp.box:
                bx, by = style.box_pad
                d.rounded_rectangle([x - bx, y - by, x + w + bx, y + lh * 0.85 + by], radius=int(style.box_radius), fill=sp.box)
            d.text((x, y), sp.text, font=f, fill=sp.color, stroke_width=int(style.stroke) if sp.color[3] else 0,
                   stroke_fill=style.stroke_color)
            x += w
        y += lh
    arr = np.array(img).astype(np.float32)
    arr[..., :3] *= arr[..., 3:4] / 255.0                     # premultiply
    return arr.astype(np.uint8)


def render(lines: list[list[Span]], style: TextStyle) -> np.ndarray:
    b = _backend()
    if b == "coretext":
        return _render_coretext(lines, style)
    if b == "skia":
        from . import text_skia

        return text_skia.render(lines, style)
    return _render_pillow(lines, style)


def measure(text: str, style: TextStyle, scale: float = 1.0) -> float:
    b = _backend()
    if b == "coretext":
        return ct_measure(text, style.font, style.size * scale)
    if b == "skia":
        from . import text_skia

        return text_skia.measure(text, style.font, style.size * scale)
    return _pil_font(int(style.size * scale)).getlength(text)


def wrap(words: list[str], style: TextStyle, max_width: float) -> list[list[int]]:
    """Greedy word wrap; returns lists of word indices per line."""
    lines: list[list[int]] = [[]]
    width = 0.0
    space = measure(" ", style)
    for i, w in enumerate(words):
        ww = measure(w, style)
        if lines[-1] and width + space + ww > max_width:
            lines.append([i])
            width = ww
        else:
            width += (space if lines[-1] else 0) + ww
            lines[-1].append(i)
    return [l for l in lines if l]
