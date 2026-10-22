"""Renders the AutoShorts icon (frontend/public/icon.svg) at every size macOS wants."""
import sys
from pathlib import Path

import skia

SVG = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icon.svg"


def draw(n: int, out: Path) -> None:
    dom = skia.SVGDOM.MakeFromStream(skia.Stream.MakeFromFile(str(SVG)))
    dom.setContainerSize(skia.Size(512, 512))
    surface = skia.Surface(n, n)
    canvas = surface.getCanvas()
    canvas.clear(skia.ColorTRANSPARENT)
    canvas.scale(n / 512, n / 512)
    dom.render(canvas)
    surface.makeImageSnapshot().save(str(out), skia.kPNG)


out = Path(sys.argv[1])
for size in (16, 32, 64, 128, 256, 512):
    draw(size, out / f"icon_{size}x{size}.png")
    draw(size * 2, out / f"icon_{size}x{size}@2x.png")
