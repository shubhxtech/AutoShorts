"""Draws the ClipForge app icon at every size macOS wants."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def draw(n: int) -> Image.Image:
    s = 4  # supersample
    N = n * s
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(N * 0.09)
    d.rounded_rectangle([pad, pad, N - pad, N - pad], radius=int(N * 0.22), fill=(20, 20, 19, 255))
    # gradient stroke phone
    pw, ph = int(N * 0.36), int(N * 0.62)
    x0, y0 = (N - pw) // 2, (N - ph) // 2
    for i in range(int(N * 0.045)):
        t = i / (N * 0.045)
        c = (255, int(212 - 90 * t), int(0 + 26 * t), 255)
        d.rounded_rectangle([x0 + i, y0 + i, x0 + pw - i, y0 + ph - i], radius=int(N * 0.07), outline=c, width=1)
    cx, cy, r = N // 2, N // 2, int(N * 0.11)
    d.polygon([(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r, cy)], fill=(255, 170, 20, 255))
    return img.resize((n, n), Image.LANCZOS)


out = Path(sys.argv[1])
for size in (16, 32, 64, 128, 256, 512):
    draw(size).save(out / f"icon_{size}x{size}.png")
    draw(size * 2).save(out / f"icon_{size}x{size}@2x.png")
