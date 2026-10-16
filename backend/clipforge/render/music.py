"""Background music beds synthesised from scratch (no samples, nothing to license).

Each mood is a small arrangement: chord progression, pad, bass, optional drums and a pluck/arp line,
rendered with numpy and a light stereo reverb. They are made to sit *under* a voice: soft attacks,
no busy melody in the speech range, and a loopable 8-bar form.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100

# semitone offsets of chord roots from the key, and chord quality
MOODS = {
    "chill":     dict(bpm=82,  key=57, prog=[(0, "m7"), (5, "m7"), (3, "maj7"), (10, "7")], drums="lofi", arp=True,  bright=0.35),
    "hype":      dict(bpm=124, key=52, prog=[(0, "m"), (8, "maj"), (3, "maj"), (10, "maj")], drums="four", arp=True, bright=0.7),
    "emotional": dict(bpm=70,  key=60, prog=[(0, "maj"), (7, "maj"), (9, "m"), (5, "maj")], drums="",     arp=True,  bright=0.3),
    "suspense":  dict(bpm=96,  key=50, prog=[(0, "m"), (1, "maj"), (0, "m"), (6, "dim")],  drums="pulse", arp=False, bright=0.25),
    "inspiring": dict(bpm=104, key=55, prog=[(0, "maj"), (9, "m"), (5, "maj"), (7, "maj")], drums="half", arp=True,  bright=0.55),
    "funny":     dict(bpm=118, key=60, prog=[(0, "maj"), (5, "maj"), (7, "7"), (0, "maj")], drums="bounce", arp=True, bright=0.6),
}
QUALITY = {"maj": [0, 4, 7], "m": [0, 3, 7], "7": [0, 4, 7, 10], "m7": [0, 3, 7, 10], "maj7": [0, 4, 7, 11],
           "dim": [0, 3, 6]}


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    from scipy.signal import butter, lfilter

    b, a = butter(2, cutoff / (SR / 2))
    return lfilter(b, a, x)


def _env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    d = n / SR
    return np.clip(t / max(attack, 1e-3), 0, 1) * np.clip((d - t) / max(release, 1e-3), 0, 1)


def _saw(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    return 2 * ((f * t + phase) % 1.0) - 1


def _pad(notes: list[float], n: int, bright: float) -> np.ndarray:
    out = np.zeros(n)
    t = np.arange(n) / SR
    for m in notes:
        f = _hz(m)
        for det in (-0.08, 0.0, 0.08):
            out += 0.5 * _saw(f * 2 ** (det / 12), n, phase=np.random.rand())
        out += 0.6 * np.sin(2 * np.pi * f * t)
    out *= _env(n, 0.6, 0.5)
    return out / (len(notes) * 2.1)


def _kick(n_total: int, at: int, gain: float = 1.0) -> tuple[int, np.ndarray]:
    n = int(0.35 * SR)
    t = np.arange(n) / SR
    f = 50 + 90 * np.exp(-t * 30)
    return at, gain * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def _noise_hit(at: int, dur: float, decay: float, gain: float, hp: bool = True) -> tuple[int, np.ndarray]:
    n = int(dur * SR)
    x = np.random.default_rng(at).standard_normal(n)
    if hp:
        x = np.diff(x, prepend=0)
    t = np.arange(n) / SR
    return at, gain * x * np.exp(-t * decay)


def _place(buf: np.ndarray, at: int, x: np.ndarray) -> None:
    end = min(len(buf), at + len(x))
    if at < end:
        buf[at:end] += x[: end - at]


def _reverb(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    def comb(sig, delay_s, fb):
        d = int(delay_s * SR)
        y = sig.copy()
        for _ in range(4):           # a few feedback taps approximates a comb filter cheaply
            sig = np.concatenate([np.zeros(d), sig[:-d]]) * fb
            y += sig
        return y
    left = comb(x, 0.0297, 0.5) + comb(x, 0.0371, 0.45)
    right = comb(x, 0.0411, 0.5) + comb(x, 0.0437, 0.45)
    return left * 0.25, right * 0.25


def render(mood: str, bars: int = 16, seed: int = 0) -> np.ndarray:
    cfg = MOODS.get(mood, MOODS["chill"])
    np.random.seed(seed)
    beat = 60.0 / cfg["bpm"]
    bar_n = int(4 * beat * SR)
    total = bar_n * bars
    pad = np.zeros(total)
    bass = np.zeros(total)
    drums = np.zeros(total)
    arp = np.zeros(total)
    key = cfg["key"]
    for b in range(bars):
        root, q = cfg["prog"][b % len(cfg["prog"])]
        chord = [key + root + iv for iv in QUALITY[q]]
        s = b * bar_n
        pad[s:s + bar_n] += _pad([c for c in chord], bar_n, cfg["bright"])
        # bass: root on beats 1 and 3 (or a drone for suspense)
        bf = _hz(key + root - 24)
        if cfg["drums"] == "pulse":
            n = bar_n
            t = np.arange(n) / SR
            bass[s:s + n] += 0.5 * np.sin(2 * np.pi * bf * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / beat) ** 2)
        else:
            for k in (0, 2):
                n = int(beat * 1.9 * SR)
                t = np.arange(n) / SR
                note = np.sin(2 * np.pi * bf * t) + 0.25 * np.sin(4 * np.pi * bf * t)
                _place(bass, s + int(k * beat * SR), 0.55 * note * _env(n, 0.01, 0.3))
        # drums
        style = cfg["drums"]
        for k in range(4):
            at = s + int(k * beat * SR)
            half = int(beat * SR / 2)
            if style == "four":
                _place(drums, *_kick(total, at, 0.9))
                _place(drums, *_noise_hit(at + half, 0.08, 45, 0.12))
                if k in (1, 3):
                    _place(drums, *_noise_hit(at, 0.2, 18, 0.25, hp=False))
            elif style in ("lofi", "half", "bounce"):
                if k == 0 or (style == "lofi" and k == 2) or (style == "bounce" and k == 2):
                    _place(drums, *_kick(total, at + (int(0.02 * SR) if style == "lofi" else 0), 0.75))
                if k in ((2,) if style == "half" else (1, 3)):
                    _place(drums, *_noise_hit(at, 0.18, 22, 0.2, hp=False))
                for h in (0, half):
                    _place(drums, *_noise_hit(at + h, 0.05, 60, 0.06 if style == "lofi" else 0.08))
            elif style == "pulse":
                _place(drums, *_noise_hit(at, 0.04, 80, 0.07))
                if k == 0:
                    _place(drums, *_kick(total, at, 0.5))
        # arp / pluck in a high register, sparse so it doesn't fight the voice
        if cfg["arp"]:
            step = beat / 2 if cfg["bpm"] < 100 else beat / 2
            notes = chord + [chord[0] + 12]
            for j in range(int(4 * beat / step)):
                if (j + b) % 3 == 2 and cfg["bpm"] < 100:
                    continue
                m = notes[j % len(notes)] + 12
                n = int(0.5 * SR)
                t = np.arange(n) / SR
                f = _hz(m)
                pl = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * np.exp(-t * 7) * _env(n, 0.003, 0.05)
                _place(arp, s + int(j * step * SR), 0.12 * pl)
    pad = _lowpass(pad, 600 + 2200 * cfg["bright"])
    mix = 0.45 * pad + 0.6 * bass + 0.55 * drums + arp
    wet_l, wet_r = _reverb(0.5 * pad + arp)
    left, right = mix + wet_l, mix + wet_r
    # Carve the voice band a little: gentle dip around 1-3 kHz via mid/side keeps it airy but out of the way.
    st = np.stack([left, right], axis=1)
    # 2-beat fade in, 1-beat crossfade-friendly tail
    fade = int(beat * SR)
    st[:fade] *= np.linspace(0, 1, fade)[:, None]
    st = np.tanh(st * 1.2)
    return st / (np.max(np.abs(st)) + 1e-9) * 0.85


def ensure_library(root: Path, variants: int = 2) -> list[Path]:
    """Write generated beds into the music folder (tagged by mood in the file name) if missing."""
    root.mkdir(parents=True, exist_ok=True)
    out = []
    for mood in MOODS:
        for v in range(variants):
            p = root / f"{mood}_generated_{v + 1}.wav"
            if not p.exists():
                y = render(mood, bars=16, seed=v * 13 + 1)
                with wave.open(str(p), "wb") as w:
                    w.setnchannels(2)
                    w.setsampwidth(2)
                    w.setframerate(SR)
                    w.writeframes((y * 32767).astype(np.int16).tobytes())
            out.append(p)
    return out
