"""Generate a rights-free demo "podcast episode" to try the pipeline end to end.

Two macOS voices talk over a two-shot of two faces, with a hard cut to a
close-up halfway through, pauses and filler words (so the jump cuts and
filler removal have something to do).

    python scripts/make_demo_episode.py ~/Movies/clipforge_demo.mp4
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import cv2
import numpy as np

SCRIPT = [
    ("Samantha", "Welcome back to the show. Today we're talking about money, habits, and the stuff nobody teaches you."),
    ("Daniel", "Um, yeah. And honestly, I think most people get this completely wrong."),
    ("Samantha", "Okay, so what's the biggest mistake?"),
    ("Daniel", "Nobody tells you this, but the first million is the hardest money you will ever make. [[slnc 900]] "
               "I was broke at twenty seven. I had two hundred dollars in my account and a kid on the way."),
    ("Samantha", "Wow. Two hundred dollars?"),
    ("Daniel", "Two hundred dollars. And I made one decision that changed everything. I stopped trading time for money. "
               "[[slnc 800]] I started building one thing that could sell while I was asleep."),
    ("Samantha", "Haha, while you were asleep. That's the dream, right?"),
    ("Daniel", "Within eighteen months we were doing a million a year. Same hours. Totally different leverage. "
               "That's the whole secret. Leverage beats effort, every single time."),
    ("Samantha", "Um, so let me push back on that. Isn't that just luck?"),
    ("Daniel", "No, and here's why. [[slnc 700]] Luck is when you win once. A system is when you win on purpose. "
               "If you do one thing after this episode, write down the one task you do every week that a system could do for you."),
    ("Samantha", "Okay, that's actually really useful. What about habits? Because I think people try to change ten things at once."),
    ("Daniel", "Exactly. The worst habit advice is to start big. Start so small it feels stupid. Two minutes a day. "
               "Your brain doesn't resist two minutes. And after thirty days, you're a different person."),
    ("Samantha", "Two minutes a day. I love that. Nobody talks about how boring success actually is."),
    ("Daniel", "Success is boring. Failure is exciting. That's the problem. [[slnc 600]] Most people quit because it's boring, not because it's hard."),
    ("Samantha", "That might be the best line we've ever had on this show. Let's take a quick break."),
]


def tts(voice: str, text: str, out: Path) -> None:
    aiff = out.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", "185", "-o", str(aiff), text], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "48000", "-ac", "1",
                    str(out)], check=True)
    aiff.unlink()


def read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def face_image() -> np.ndarray:
    try:
        from skimage import data

        return data.astronaut()[..., ::-1].copy()
    except ImportError:
        img = np.full((512, 512, 3), 200, np.uint8)
        cv2.circle(img, (256, 230), 150, (180, 200, 230), -1)
        return img


def main(out_path: str) -> None:
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    sr = 48000
    pieces, turns, t = [], [], 0.0
    for k, (voice, line) in enumerate(SCRIPT):
        p = tmp / f"{k}.wav"
        tts(voice, line, p)
        a = read_wav(p)
        pieces.append(a)
        turns.append((t, t + len(a) / sr, 0 if voice == "Samantha" else 1))
        t += len(a) / sr
        gap = np.zeros(int(sr * 0.35), np.float32)
        pieces.append(gap)
        t += len(gap) / sr
    audio = np.concatenate(pieces)
    duration = len(audio) / sr
    wav = tmp / "episode.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    fps, W, H = 30, 1920, 1080
    face = face_image()
    a = cv2.resize(face, (520, 520))
    b = cv2.resize(face[:, ::-1], (520, 520))
    close = cv2.resize(face, (980, 980))
    cut_at = (duration * 0.45, duration * 0.62)
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-i", str(wav), "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                            str(out)], stdin=subprocess.PIPE)
    grad = np.linspace(40, 90, H, dtype=np.float32)[:, None, None] * np.array([1.0, 0.8, 0.6])[None, None, :]
    bg = np.broadcast_to(grad, (H, W, 3)).astype(np.uint8).copy()
    for i in range(int(duration * fps)):
        tt = i / fps
        frame = bg.copy()
        drift = int(18 * np.sin(tt * 0.4))
        if cut_at[0] <= tt < cut_at[1]:
            y0, x0 = (H - 980) // 2 + 60, (W - 980) // 2 + drift
            frame[max(0, y0):y0 + 980, x0:x0 + 980] = close[: H - max(0, y0)] if y0 + 980 > H else close
        else:
            frame[260:780, 180 + drift:700 + drift] = a
            frame[260:780, 1220 - drift:1740 - drift] = b
        enc.stdin.write(frame.tobytes())
    enc.stdin.close()
    enc.wait()
    print(f"wrote {out} ({duration:.0f}s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "~/Movies/clipforge_demo.mp4")
