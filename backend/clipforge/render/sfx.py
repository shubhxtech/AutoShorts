"""Sound effects synthesised from scratch, so there is nothing to license."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from ..config import paths

SR = 48000


def _biquad_bandpass(x: np.ndarray, centers: np.ndarray, q: float = 1.2) -> np.ndarray:
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    block = 128
    for start in range(0, len(x), block):
        f0 = float(centers[min(start, len(centers) - 1)])
        w0 = 2 * np.pi * f0 / SR
        alpha = np.sin(w0) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w0), 1 - alpha
        b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
        for n in range(start, min(start + block, len(x))):
            xn = x[n]
            yn = b0 * xn + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, xn
            y2, y1 = y1, yn
            y[n] = yn
    return y


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    return np.convolve(x, np.ones(k) / k, mode="same")


def whoosh(d: float = 0.55) -> np.ndarray:
    """Soft air whoosh: pink-ish noise through a gentle band sweep, slow swell."""
    n = int(SR * d)
    rng = np.random.default_rng(7)
    noise = _smooth(rng.standard_normal(n), 3)
    t = np.linspace(0, 1, n)
    centers = 250 * (8 ** np.sin(np.pi * t * 0.85))
    y = _biquad_bandpass(noise, centers, q=0.6)
    env = np.sin(np.pi * np.clip(t * 1.1, 0, 1)) ** 3
    return _smooth(y * env, 5)


def pop(d: float = 0.09) -> np.ndarray:
    """Soft 'bloop' rather than a click."""
    n = int(SR * d)
    t = np.arange(n) / SR
    f = 620 * np.exp(-t * 22) + 220
    env = np.clip(t * 600, 0, 1) * np.exp(-t * 45)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env


def hit(d: float = 0.6) -> np.ndarray:
    """Low, rounded thump for a card landing (no harsh transient)."""
    n = int(SR * d)
    t = np.arange(n) / SR
    f = 70 * np.exp(-t * 5) + 42
    env = np.clip(t * 250, 0, 1) * np.exp(-t * 6.5)
    return np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * env * 1.3)


def ding(d: float = 0.9) -> np.ndarray:
    n = int(SR * d)
    t = np.arange(n) / SR
    tone = np.sin(2 * np.pi * 1318.5 * t) + 0.45 * np.sin(2 * np.pi * 2637 * t) + 0.2 * np.sin(2 * np.pi * 3951 * t)
    return tone * np.exp(-t * 6) * np.clip(t * 400, 0, 1)


GENERATORS = {"whoosh": whoosh, "pop": pop, "hit": hit, "ding": ding}


def path_for(kind: str) -> Path:
    d = paths().home / "sfx"
    d.mkdir(exist_ok=True)
    p = d / f"{kind}_v2.wav"
    if not p.exists():
        y = GENERATORS[kind]()
        y = y / (np.max(np.abs(y)) + 1e-9) * 0.89
        stereo = np.stack([y, y], axis=1)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((stereo * 32767).astype(np.int16).tobytes())
    return p
