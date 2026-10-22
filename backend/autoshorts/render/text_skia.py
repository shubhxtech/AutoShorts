"""Cross-platform text rasterizer (Skia + HarfBuzz) used on Windows and Linux.

Mirrors the CoreText backend: same layout maths, per-span colours, highlight boxes, stroke, shadow and
background box, so captions look the same on every OS. Shaping comes from HarfBuzz, so Devanagari
(and any other script) renders correctly, and emoji fall back to the system colour-emoji font.

The Mac fonts the editor asks for by PostScript name are mapped to open-licensed look-alikes
(Montserrat, Anton, Noto) that are downloaded once into the data folder.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from .. import logs

log = logs.get("text")

# OFL fonts, fetched on first use. Static weights so the layout engine picks the right one.
FONT_FILES = {
    "Montserrat-SemiBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-SemiBold.ttf",
    "Montserrat-ExtraBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
    "Montserrat-Black.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Black.ttf",
    "Anton-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Bold.ttf",
    "NotoSerif-Bold.ttf": "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSerif/hinted/ttf/NotoSerif-Bold.ttf",
}

# PostScript name (what the editor asks for) -> (families to try, weight, condensed)
FONT_MAP = {
    "AvenirNext-Heavy": (["Avenir Next", "Montserrat", "Segoe UI Black", "Arial Black"], 800, False),
    "AvenirNext-DemiBold": (["Avenir Next", "Montserrat", "Segoe UI Semibold", "Arial"], 600, False),
    "Arial-Black": (["Arial Black", "Montserrat", "Segoe UI Black"], 900, False),
    "Futura-CondensedExtraBold": (["Futura", "Anton", "Impact", "Arial Narrow"], 800, True),
    "Georgia-Bold": (["Georgia", "Noto Serif", "DejaVu Serif"], 700, False),
    "NewYork-Bold": (["New York", "Georgia", "Noto Serif"], 700, False),
}
# Tried after the main family, glyph by glyph: Devanagari, then colour emoji, then anything.
FALLBACK_FAMILIES = ["Noto Sans Devanagari", "Nirmala UI", "Kohinoor Devanagari", "Mangal",
                     "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", "Segoe UI Symbol", "DejaVu Sans"]


def available() -> bool:
    try:
        import skia  # noqa: F401
        from skia import textlayout  # noqa: F401
        return True
    except ImportError:
        return False


def _font_dir() -> Path:
    from ..config import paths

    d = paths().fonts
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_fonts() -> list[Path]:
    """Download the open fonts once (best effort: system fonts still work without them)."""
    import httpx

    out = []
    for name, url in FONT_FILES.items():
        p = _font_dir() / name
        if not p.exists():
            try:
                r = httpx.get(url, timeout=30, follow_redirects=True)
                r.raise_for_status()
                p.write_bytes(r.content)
                log.info("downloaded font %s", name)
            except Exception as exc:
                log.warning("couldn't download %s (%s); using system fonts", name, exc)
                continue
        out.append(p)
    return out


# System fonts that cover what the open fonts don't (colour emoji, Devanagari), per OS.
SYSTEM_FALLBACK_FILES = [
    "C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/Nirmala.ttc", "C:/Windows/Fonts/NirmalaB.ttc",
    "C:/Windows/Fonts/seguisym.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc", "/System/Library/Fonts/Kohinoor.ttc",
]


@lru_cache(maxsize=1)
def _collection():
    import skia
    from skia import textlayout

    provider = textlayout.TypefaceFontProvider()
    n = 0
    for p in [*ensure_fonts(), *(Path(x) for x in SYSTEM_FALLBACK_FILES if Path(x).exists())]:
        tf = skia.Typeface.MakeFromFile(str(p))
        if tf is not None:
            provider.registerTypeface(tf)
            n += 1
    log.info("text: %d font files registered", n)
    fc = textlayout.FontCollection()
    fc.setDefaultFontManager(provider)
    return fc


def _style_for(font: str):
    fams, weight, condensed = FONT_MAP.get(font, ([font, "Montserrat", "Arial Black"], 800, False))
    return fams + FALLBACK_FAMILIES, weight, condensed


def _color(c) -> int:
    import skia

    return skia.Color(int(c[0]), int(c[1]), int(c[2]), int(c[3]))


def _paragraph(spans, style, mode: str):
    """mode: fill | stroke | measure"""
    import skia
    from skia import textlayout

    pstyle = textlayout.ParagraphStyle()
    builder = textlayout.ParagraphBuilder.make(pstyle, _collection(), skia.Unicode())
    fams, weight, condensed = _style_for(style.font)
    for sp in spans:
        ts = textlayout.TextStyle()
        ts.setFontFamilies(fams)
        ts.setFontSize(style.size * sp.scale)
        ts.setFontStyle(skia.FontStyle(weight, skia.FontStyle.kCondensed_Width if condensed else skia.FontStyle.kNormal_Width,
                                       skia.FontStyle.kUpright_Slant))
        paint = skia.Paint(AntiAlias=True)
        if mode == "stroke":
            paint.setStyle(skia.Paint.kStroke_Style)
            paint.setStrokeWidth(style.stroke * 2)
            paint.setStrokeJoin(skia.Paint.kRound_Join)
            paint.setColor(_color(style.stroke_color if sp.color[3] > 0 else (0, 0, 0, 0)))
        else:
            paint.setColor(_color(sp.color))
        ts.setForegroundPaint(paint)
        builder.pushStyle(ts)
        builder.addText(sp.text)
        builder.pop()
    para = builder.Build()
    para.layout(100000)
    return para


def _metrics(para) -> tuple[float, float, float]:
    """(advance width incl. spaces, ascent, descent)."""
    base = float(para.AlphabeticBaseline)
    return float(para.MaxIntrinsicWidth), base, float(para.Height) - base


def measure(text: str, font: str, size: float) -> float:
    from .text import Span, TextStyle

    return _metrics(_paragraph([Span(text)], TextStyle(font=font, size=size), "measure"))[0]


def render(lines, style) -> np.ndarray:
    import skia
    fill = [_paragraph(sp, style, "fill") for sp in lines]
    stroke = [_paragraph(sp, style, "stroke") for sp in lines] if style.stroke > 0 else None
    metrics = [_metrics(p) for p in fill]
    pad = int(style.stroke * 2 + style.shadow * 2 + 8)
    bg_px, bg_py = style.background_pad if style.background else (0, 0)
    line_h = [max(m[1] + m[2], style.size * 0.9) * style.line_spacing for m in metrics]
    text_w = max((m[0] for m in metrics), default=1)
    W = int(text_w + 2 * pad + 2 * bg_px) + 2
    H = int(sum(line_h) + 2 * pad + 2 * bg_py) + 2
    info = skia.ImageInfo.Make(W, H, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
    surface = skia.Surface.MakeRaster(info)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)

    def rrect(x, y, w, h, r, color):
        r = min(r, w / 2, h / 2)
        canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r),
                         skia.Paint(AntiAlias=True, Color=_color(color)))

    if style.background:
        rrect(pad, pad, W - 2 * pad, H - 2 * pad, style.background_radius, style.background)
    y = pad + bg_py
    tops = []
    for (_w, asc, desc), lh in zip(metrics, line_h):
        extra = (lh - (asc + desc)) / 2
        tops.append(y + extra)          # top of the line box; the paragraph's own origin
        y += lh
    for li, spans in enumerate(lines):
        w, asc, desc = metrics[li]
        x = (W - w) / 2
        if any(sp.box for sp in spans):
            for k, sp in enumerate(spans):
                if sp.box:
                    a = _metrics(_paragraph(spans[:k], style, "measure"))[0] if k else 0.0
                    b = a + _metrics(_paragraph([sp], style, "measure"))[0]
                    bx, by = style.box_pad
                    rrect(x + a - bx, tops[li] - by, (b - a) + 2 * bx, asc + desc + 2 * by, style.box_radius, sp.box)
    first = stroke or fill
    if style.shadow > 0:
        sc = style.shadow_color
        shadow = skia.ImageFilters.DropShadowOnly(style.shadow_offset[0], -style.shadow_offset[1], style.shadow / 2,
                                                  style.shadow / 2, _color(sc))
        canvas.saveLayer(None, skia.Paint(ImageFilter=shadow))
        for li, para in enumerate(first):
            para.paint(canvas, (W - metrics[li][0]) / 2, tops[li])
        canvas.restore()
    if stroke:
        for li, para in enumerate(stroke):
            para.paint(canvas, (W - metrics[li][0]) / 2, tops[li])
    for li, para in enumerate(fill):
        para.paint(canvas, (W - metrics[li][0]) / 2, tops[li])
    arr = surface.toarray(colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kPremul_AlphaType)
    return np.ascontiguousarray(arr)
