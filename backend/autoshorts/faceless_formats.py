"""Faceless video formats and the shot-based assembler.

Each format is a proven short-form template (story, countdown, quiz, would-you-rather, text story,
quote, rapid facts). A format decides how the script is written, which on-screen graphics are drawn,
how long the viewer gets to think, and which sounds play. The assembler turns scenes into fast,
varied shots: every scene is split into 1-3 shots with different camera moves, scene changes get a
whip or flash, and the whole thing is graded with a soft vignette.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from . import logs
from .media import ffmpeg_bin
from .render import sfx as SFX
from .render import text as T

log = logs.get("faceless")

W, H, FPS = 1080, 1920, 30


@dataclass(frozen=True)
class Format:
    id: str
    name: str
    blurb: str
    example: str
    prompt: str                 # format-specific writing rules for the LLM
    style: str = "cinematic"    # default visual style
    mood: str = "inspiring"     # default music mood
    seconds: int = 45
    captions: bool = True       # spoken captions on top (off when the graphics already show the words)
    think_s: float = 0.0        # silent thinking time after question/choice scenes


FORMATS: dict[str, Format] = {f.id: f for f in [
    Format("story", "Story", "A narrated mini-documentary: hook, rising tension, twist, payoff.",
           "The stone age surgeon who saved a child 31,000 years ago",
           "Write 7-11 scenes (kind='beat'). Scene 1 is the spoken hook. Build tension, include one surprising "
           "turn, end on a satisfying payoff. Give most scenes a 'visual2': a second shot of the same moment from a "
           "different angle or a close-up detail, so the video can cut every 2 seconds.",
           style="cinematic", mood="suspense"),
    Format("countdown", "Top N countdown", "Numbered list that counts down to #1, with a big number on screen.",
           "Top 5 most dangerous animals in India",
           "Scene 1 (kind='intro'): the hook that promises the list. Then one scene per item (kind='item') counting "
           "DOWN, with 'number' (5,4,3,2,1) and 'heading' = the item name in max 4 words; narration says why in 1-2 "
           "punchy sentences. Save the most surprising item for #1. Each item's visual shows the item itself, large and clearly (an animal, place or object), never people looking at it. Last scene (kind='outro'): a one-line CTA.",
           style="cinematic", mood="hype"),
    Format("facts", "Rapid facts", "Quick-fire 'did you know' facts, one per scene, numbered.",
           "7 facts about the human brain that sound fake",
           "Scene 1 (kind='intro'): the hook. Then 5-8 facts (kind='item') with 'number' counting UP from 1 and "
           "'heading' = the fact's key phrase (max 5 words). One sentence each, surprising and verifiable. Each visual shows the subject of the fact itself, not people. "
           "Last scene (kind='outro'): ask which fact surprised them most.",
           style="3d", mood="funny", seconds=40),
    Format("quiz", "Quiz", "Question, 3 options, a ticking timer, then the answer reveal.",
           "Only 1% can answer these geography questions",
           "Scene 1 (kind='intro'): a challenge hook ('Only 1% get all 4'). Then 3-4 rounds, each two scenes: "
           "(a) kind='question': 'heading' = the question (max 10 words), 'options' = exactly 3 short answers, "
           "'answer' = index 0-2 of the correct one, narration reads the question and the options; "
           "(b) kind='answer': narration reveals the answer and one fun fact. Last scene (kind='outro'): "
           "'Comment your score'. Questions must have one unambiguous correct answer taken from the SOURCE FACTS; put the exact supporting sentence in 'evidence'. Never ask about 'firsts', 'largest' or dates unless the source states them plainly.",
           style="3d", mood="funny", seconds=50, captions=False, think_s=3.0),
    Format("would_you_rather", "Would you rather", "Two choices split top/bottom; viewers comment A or B.",
           "Would you rather: superpower edition",
           "Scene 1 (kind='intro'): hook ('Pick fast. No changing.'). Then 4-5 dilemmas, each one scene with "
           "kind='choice', 'options' = exactly 2 short choices (max 6 words each), 'visual' = picture of option A, "
           "'visual2' = picture of option B, narration 'Would you rather A... or B?'. Make both sides tempting. "
           "Last scene (kind='outro'): 'Comment A or B for each.' Never state what percentage of people chose what.",
           style="3d", mood="funny", seconds=45, captions=False, think_s=2.5),
    Format("text_story", "Text story", "First-person story told over a post card, casual and gripping.",
           "My roommate secretly ran a business from our apartment",
           "A first-person, casual, gripping FICTIONAL story. Scene 1 (kind='intro'): 'heading' = the post title "
           "(max 12 words) and narration reads it. Then 7-10 beats (kind='beat') with cliffhanger pacing and a twist "
           "ending. Visuals are atmospheric scenes from the story (no readable text).",
           style="cinematic", mood="suspense", seconds=55),
    Format("quote", "Motivational quote", "Big kinetic words over moody visuals, deep slow voice.",
           "Discipline is choosing what you want most over what you want now",
           "5-7 scenes (kind='beat'), each 'heading' = one powerful line of max 12 words, narration = the same line "
           "(you may add a few words). Build from struggle to resolve. Visuals: moody, cinematic, atmospheric "
           "(silhouettes, landscapes, rain, light), no faces.",
           style="cinematic", mood="emotional", seconds=35, captions=False),
]}


# ---------------------------------------------------------------- shared look

ACCENT = (255, 214, 10, 255)        # RGBA yellow
RED = (239, 68, 68, 255)
BLUE = (59, 130, 246, 255)
GREEN = (34, 197, 94, 255)
WHITE = (255, 255, 255, 255)
INK = (18, 18, 18, 255)


def _heavy() -> str:
    return T.resolve_font("AvenirNext-Heavy", "Arial-Black")


def _ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _back(x: float, s: float = 1.9) -> float:
    x = max(0.0, min(1.0, x)) - 1
    return x * x * ((s + 1) * x + s) + 1


@lru_cache(maxsize=1)
def _vignette() -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H / 2) / (H * 0.72)) ** 2)
    return np.clip(1.05 - 0.45 * d ** 2, 0.55, 1.0)[..., None].astype(np.float32)


def _place(frame: np.ndarray, spr: np.ndarray, cx: float, cy: float, scale: float = 1.0, opacity: float = 1.0) -> None:
    spr = T.scale_sprite(spr, scale)
    h, w = spr.shape[:2]
    T.blend(frame, spr, int(cx - w / 2), int(cy - h / 2), opacity)


def _wrap_lines(text: str, size: float, max_w: float, font: str) -> list[str]:
    st = T.TextStyle(font=font, size=size)
    words = text.split()
    return [" ".join(words[i] for i in line) for line in T.wrap(words, st, max_w)] if words else []


@lru_cache(maxsize=64)
def _number_badge(n: int) -> np.ndarray:
    st = T.TextStyle(font=T.resolve_font("Futura-CondensedExtraBold", _heavy()), size=230, stroke=12,
                     stroke_color=INK, shadow=18)
    return T.render([[T.Span(f"#{n}", ACCENT)]], st)


@lru_cache(maxsize=64)
def _heading_chip(text: str, bg: tuple = INK, fg: tuple = WHITE, size: int = 74) -> np.ndarray:
    font = _heavy()
    lines = _wrap_lines(text.upper(), size, W - 220, font)[:3]
    st = T.TextStyle(font=font, size=size, background=bg, background_pad=(40, 22), background_radius=22,
                     line_spacing=1.04)
    return T.render([[T.Span(l, fg)] for l in lines], st)


@lru_cache(maxsize=32)
def _question_card(question: str) -> np.ndarray:
    font = _heavy()
    lines = _wrap_lines(question, 70, W - 260, font)[:4]
    st = T.TextStyle(font=font, size=70, background=(255, 255, 255, 250), background_pad=(56, 46),
                     background_radius=36, line_spacing=1.1)
    return T.render([[T.Span(l, INK)] for l in lines], st)


@lru_cache(maxsize=64)
def _option_pill(label: str, text: str, state: str) -> np.ndarray:
    """state: idle | right | wrong"""
    bg = {"idle": (255, 255, 255, 235), "right": GREEN, "wrong": (90, 90, 90, 200)}[state]
    fg = INK if state == "idle" else WHITE
    tag = {"idle": ACCENT, "right": WHITE, "wrong": (200, 200, 200, 255)}[state]
    st = T.TextStyle(font=_heavy(), size=58, background=bg, background_pad=(40, 24), background_radius=60)
    mark = "  ✓" if state == "right" else ""
    return T.render([[T.Span(f"{label}  ", tag if state != "idle" else (217, 119, 6, 255)), T.Span(text + mark, fg)]], st)


@lru_cache(maxsize=16)
def _timer_digit(n: int) -> np.ndarray:
    st = T.TextStyle(font=_heavy(), size=110)
    return T.render([[T.Span(str(n), WHITE)]], st)


@lru_cache(maxsize=16)
def _post_card(title: str) -> np.ndarray:
    font = _heavy()
    demi = T.resolve_font("AvenirNext-DemiBold", font)
    lines = _wrap_lines(title, 60, W - 300, font)[:5]
    body = T.render([[T.Span(l, INK)] for l in lines], T.TextStyle(font=font, size=60, line_spacing=1.12))
    head = T.render([[T.Span("Anonymous", INK), T.Span("  ·  story time", (120, 120, 120, 255), 0.8)]],
                    T.TextStyle(font=demi, size=44))
    foot = T.render([[T.Span("♥      💬      ↗", (110, 110, 110, 255))]], T.TextStyle(font=demi, size=44))
    pad = 56
    w = max(body.shape[1], head.shape[1] + 110) + pad * 2
    h = head.shape[0] + body.shape[0] + foot.shape[0] + pad * 2 + 60
    card = np.zeros((h, w, 4), np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.rectangle(mask, (30, 0), (w - 31, h - 1), 255, -1)
    cv2.rectangle(mask, (0, 30), (w - 1, h - 31), 255, -1)
    for cx, cy in ((30, 30), (w - 31, 30), (30, h - 31), (w - 31, h - 31)):
        cv2.circle(mask, (cx, cy), 30, 255, -1, lineType=cv2.LINE_AA)
    card[..., :3] = mask[..., None]
    card[..., 3] = mask
    # avatar
    cv2.circle(card, (pad + 40, pad + head.shape[0] // 2), 38, (255, 120, 40, 255), -1, lineType=cv2.LINE_AA)
    y = pad
    _blit(card, head, pad + 100, y)
    y += head.shape[0] + 30
    _blit(card, body, pad, y)
    y += body.shape[0] + 30
    _blit(card, foot, pad, y)
    return card


def _blit(dst: np.ndarray, spr: np.ndarray, x: int, y: int) -> None:
    h, w = spr.shape[:2]
    h, w = min(h, dst.shape[0] - y), min(w, dst.shape[1] - x)
    region = dst[y:y + h, x:x + w].astype(np.float32)
    s = spr[:h, :w].astype(np.float32)
    k = s[..., 3:4] / 255
    region[..., :3] = s[..., :3] + region[..., :3] * (1 - k)
    region[..., 3] = s[..., 3] + region[..., 3] * (1 - k[..., 0])
    dst[y:y + h, x:x + w] = np.clip(region, 0, 255).astype(np.uint8)


@lru_cache(maxsize=64)
def _quote_sprite(text: str, n_words: int) -> np.ndarray:
    font = T.resolve_font("Georgia-Bold", "NewYork-Bold", _heavy())
    words = text.split()
    st = T.TextStyle(font=font, size=96, line_spacing=1.12, shadow=24, shadow_color=(0, 0, 0, 220))
    lines = T.wrap(words, st, W - 200)
    rows = []
    for line in lines:
        rows.append([T.Span(words[i] + " ", WHITE if i < n_words else (0, 0, 0, 0)) for i in line])
    return T.render(rows, st)


# ---------------------------------------------------------------- scene graphics

@dataclass
class SceneTiming:
    start: float       # scene start (video seconds)
    speech_end: float  # narration ends (thinking time follows)
    end: float


def draw_graphics(frame: np.ndarray, fmt: str, scenes: list, k: int, tm: SceneTiming, t: float) -> None:
    sc = scenes[k]
    kind = getattr(sc, "kind", "beat") or "beat"
    u = t - tm.start
    if kind == "item" and sc.number is not None:
        a = _back(u / 0.32)
        _place(frame, _number_badge(int(sc.number)), W / 2, 470, 0.4 + 0.6 * a, min(1.0, u / 0.1))
        if sc.heading:
            b = _ease((u - 0.18) / 0.25)
            _place(frame, _heading_chip(sc.heading, bg=ACCENT, fg=INK, size=68), W / 2, 690 + 40 * (1 - b), 1.0, b)
    elif kind == "question" and sc.options:
        _draw_question(frame, sc, u, tm, reveal=None)
    elif kind == "answer":
        q = next((scenes[j] for j in range(k - 1, -1, -1) if getattr(scenes[j], "kind", "") == "question"), None)
        if q is not None and q.options:
            _draw_question(frame, q, 10.0, tm, reveal=q.answer if q.answer is not None else 0)
            flash = max(0.0, 1 - u / 0.18)
            if flash > 0:
                frame[:] = (frame.astype(np.float32) * (1 - 0.6 * flash) + 255 * 0.6 * flash).astype(np.uint8)
    elif kind == "choice" and len(sc.options) >= 2:
        _draw_choice(frame, sc, u, tm)
    elif kind == "intro" and fmt == "text_story" and sc.heading:
        a = _back(u / 0.35)
        _place(frame, _post_card(sc.heading), W / 2, H * 0.42, 0.6 + 0.4 * a, min(1.0, u / 0.12))
    elif fmt == "quote" and sc.heading:
        words = sc.heading.split()
        speak = max(0.4, tm.speech_end - tm.start)
        n = min(len(words), 1 + int(len(words) * max(0.0, u) / (speak * 0.85)))
        _place(frame, _quote_sprite(sc.heading, n), W / 2, H * 0.46)


def _draw_question(frame: np.ndarray, sc, u: float, tm: SceneTiming, reveal: int | None) -> None:
    frame[:] = (frame.astype(np.float32) * 0.5).astype(np.uint8)
    card = _question_card(sc.heading or sc.narration)
    a = _back(u / 0.3)
    _place(frame, card, W / 2, 520, 0.7 + 0.3 * a, min(1.0, u / 0.12))
    y0 = 520 + card.shape[0] / 2 + 120
    for j, opt in enumerate(sc.options[:3]):
        b = _ease((u - 0.35 - 0.18 * j) / 0.25)
        if b <= 0:
            continue
        state = "idle" if reveal is None else ("right" if j == reveal else "wrong")
        _place(frame, _option_pill("ABC"[j], opt, state), W / 2 + 60 * (1 - b), y0 + j * 170, 1.0, b)
    if reveal is None:
        think = tm.end - tm.speech_end
        if think > 0.5 and u >= tm.speech_end - tm.start:
            left = tm.end - (tm.start + u)
            _draw_timer(frame, W / 2, y0 + 3 * 170 + 80, left / think, int(np.ceil(left)))


def _draw_timer(frame: np.ndarray, cx: float, cy: float, frac: float, n: int) -> None:
    r = 92
    cv2.circle(frame, (int(cx), int(cy)), r, (30, 30, 30), -1, lineType=cv2.LINE_AA)
    cv2.ellipse(frame, (int(cx), int(cy)), (r - 8, r - 8), -90, 0, 360 * max(0.0, frac), (10, 214, 255), 14,
                lineType=cv2.LINE_AA)
    if n > 0:
        _place(frame, _timer_digit(n), cx, cy)


def _draw_choice(frame: np.ndarray, sc, u: float, tm: SceneTiming) -> None:
    for j, (label, color) in enumerate((("A", RED), ("B", BLUE))):
        cy = H * 0.5 + (-200 if j == 0 else 200)     # labels sit either side of the seam
        b = _ease((u - 0.1 - 0.35 * j) / 0.25)
        if b > 0:
            _place(frame, _heading_chip(f"{label}: {sc.options[j]}", bg=color, fg=WHITE, size=60), W / 2,
                   cy + (1 - b) * (-40 if j == 0 else 40), 1.0, b)
    # "OR" badge on the seam
    cv2.circle(frame, (W // 2, H // 2), 82, (255, 255, 255), -1, lineType=cv2.LINE_AA)
    _place(frame, _or_sprite(), W / 2, H / 2)
    think = tm.end - tm.speech_end
    if think > 0.5 and u >= tm.speech_end - tm.start:
        frac = (tm.end - (tm.start + u)) / think
        bw = int(W * max(0.0, frac))
        frame[H // 2 - 6:H // 2 + 6, :] = (40, 40, 40)
        frame[H // 2 - 6:H // 2 + 6, :bw] = (10, 214, 255)
        cv2.circle(frame, (W // 2, H // 2), 82, (255, 255, 255), -1, lineType=cv2.LINE_AA)
        _place(frame, _or_sprite(), W / 2, H / 2)


@lru_cache(maxsize=1)
def _or_sprite() -> np.ndarray:
    return T.render([[T.Span("OR", INK)]], T.TextStyle(font=_heavy(), size=72))


# ---------------------------------------------------------------- camera moves

MOVES = ["push_in", "pan_right", "pull_out", "pan_left", "tilt_down", "push_in_off"]


@dataclass
class Shot:
    image: int          # index into the loaded image list
    start: float
    end: float
    move: str
    close: bool = False  # tighter crop on the upper part (detail / reaction shot)
    half: int = -1       # would-you-rather: 0 = top half, 1 = bottom half, -1 = full frame


def _cover_affine(img_wh: tuple[int, int], out_wh: tuple[int, int], z: float, cx: float, cy: float) -> np.ndarray:
    w, h = img_wh
    ow, oh = out_wh
    s = max(ow / w, oh / h) * z
    cw, ch = ow / s, oh / s
    x0 = min(max(0.0, cx * w - cw / 2), w - cw)
    y0 = min(max(0.0, cy * h - ch / 2), h - ch)
    return np.array([[s, 0, -x0 * s], [0, s, -y0 * s]], np.float32)


def shot_frame(img: np.ndarray, shot: Shot, t: float, out_wh: tuple[int, int] = (W, H)) -> np.ndarray:
    f = _ease((t - shot.start) / max(0.1, shot.end - shot.start))
    z, cx, cy = 1.06, 0.5, 0.5
    m = shot.move
    if m == "push_in":
        z = 1.04 + 0.12 * f
    elif m == "pull_out":
        z = 1.18 - 0.12 * f
    elif m == "pan_right":
        z, cx = 1.14, 0.42 + 0.16 * f
    elif m == "pan_left":
        z, cx = 1.14, 0.58 - 0.16 * f
    elif m == "tilt_down":
        z, cy = 1.14, 0.42 + 0.14 * f
    elif m == "push_in_off":
        z, cx, cy = 1.06 + 0.16 * f, 0.5 + 0.06 * f, 0.44
    if shot.close:
        z *= 1.45
        cy = min(cy, 0.42)
    # a short punch on every hard cut so cuts land with energy
    age = t - shot.start
    if age < 0.2:
        z *= 1.0 + 0.06 * (1 - _ease(age / 0.2))
    h, w = img.shape[:2]
    M = _cover_affine((w, h), out_wh, z, cx, cy)
    return cv2.warpAffine(img, M, out_wh, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def plan_shots(fmt: str, scenes: list, timings: list[SceneTiming], image_index: list[tuple[int, int | None]]) -> list[Shot]:
    """Cut every ~2 s: alternate the scene's two images, add a close-up when there's only one."""
    shots: list[Shot] = []
    mv = 0
    for k, (sc, tm) in enumerate(zip(scenes, timings)):
        a, b = image_index[k]
        kind = getattr(sc, "kind", "beat") or "beat"
        d = tm.end - tm.start
        if kind == "choice" and b is not None:
            shots.append(Shot(a, tm.start, tm.end, "push_in", half=0))
            shots.append(Shot(b, tm.start, tm.end, "push_in", half=1))
            continue
        if kind in ("question", "answer") or fmt == "quote":
            shots.append(Shot(a, tm.start, tm.end, MOVES[mv % len(MOVES)]))
            mv += 1
            continue
        n = 1 if d < 2.6 else 2 if d < 5.0 else 3
        bounds = [tm.start + d * i / n for i in range(n + 1)]
        for i in range(n):
            if b is not None:
                img, close = (a, False) if i % 2 == 0 else (b, False)
                if i == 2:
                    img, close = a, True
            else:
                img, close = a, i % 2 == 1
            shots.append(Shot(img, bounds[i], bounds[i + 1], MOVES[mv % len(MOVES)], close=close))
            mv += 1
    return shots


# ---------------------------------------------------------------- audio

def read_audio(path: Path, sr: int = 48000) -> np.ndarray:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).copy()


def write_wav(path: Path, y: np.ndarray, sr: int = 48000) -> None:
    import wave

    y = np.clip(y, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype(np.int16).tobytes())


def _sfx(kind: str, sr: int = 48000) -> np.ndarray:
    return read_audio(SFX.path_for(kind), sr)


def mix_sfx(fmt: str, scenes: list, timings: list[SceneTiming], total: float, sr: int = 48000) -> np.ndarray:
    """A sound-design track (whooshes on scene changes, ticks while thinking, dings on reveals)."""
    y = np.zeros(int(total * sr) + sr, np.float32)

    def add(kind: str, at: float, db: float) -> None:
        s = _sfx(kind, sr) * (10 ** (db / 20))
        i = max(0, int(at * sr))
        j = min(len(y), i + len(s))
        y[i:j] += s[: j - i]

    for k, (sc, tm) in enumerate(zip(scenes, timings)):
        kind = getattr(sc, "kind", "beat") or "beat"
        if k > 0 and fmt != "quote":
            add("whoosh", tm.start - 0.18, -20)
        if kind == "item":
            add("hit", tm.start, -14)
        if kind in ("question", "choice") and tm.end - tm.speech_end > 0.5:
            t = tm.speech_end
            while t < tm.end - 0.2:
                add("pop", t, -16)
                t += 1.0
        if kind == "answer":
            add("ding", tm.start, -12)
    return y[: int(total * sr)]


# ---------------------------------------------------------------- assembly

def assemble(fmt: str, scenes: list, images: list[tuple[Path, Path | None]], timings: list[SceneTiming],
             audio: Path, out: Path, progress=None) -> None:
    """Render the faceless base video (visuals, graphics and the voice + sound design track)."""
    loaded: list[np.ndarray] = []
    index: list[tuple[int, int | None]] = []
    for a, b in images:
        loaded.append(cv2.imread(str(a)))
        ia = len(loaded) - 1
        ib = None
        if b is not None and Path(b).exists():
            loaded.append(cv2.imread(str(b)))
            ib = len(loaded) - 1
        index.append((ia, ib))
    shots = plan_shots(fmt, scenes, timings, index)
    total = timings[-1].end
    n = int(round(total * FPS))
    vig = _vignette()
    enc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            "-b:a", "192k", "-shortest", str(out)], stdin=subprocess.PIPE)
    k = 0
    try:
        for i in range(n):
            t = i / FPS
            while k < len(timings) - 1 and t >= timings[k].end:
                k += 1
            tm = timings[k]
            active = [s for s in shots if s.start <= t < s.end] or [shots[-1]]
            if active[0].half >= 0:
                frame = np.empty((H, W, 3), np.uint8)
                for s in active:
                    part = shot_frame(loaded[s.image], s, t, (W, H // 2))
                    frame[(H // 2) * s.half:(H // 2) * (s.half + 1)] = part
            else:
                frame = shot_frame(loaded[active[0].image], active[0], t)
                if fmt == "quote":
                    frame = (frame.astype(np.float32) * 0.62).astype(np.uint8)
            frame = (frame.astype(np.float32) * vig).astype(np.uint8)
            # scene-change transitions
            age = t - tm.start
            if k > 0 and age < 0.4:
                if fmt == "quote":
                    pass
                elif age < 0.16:
                    shift = int(W * 0.22 * (1 - age / 0.16))
                    frame = np.roll(frame, shift, axis=1)
                    frame = cv2.blur(frame, (int(9 + 90 * (1 - age / 0.16)), 1))
            if fmt == "quote" and k > 0 and age < 0.45:
                fade = age / 0.45
                frame = (frame.astype(np.float32) * (0.25 + 0.75 * fade)).astype(np.uint8)
            draw_graphics(frame, fmt, scenes, k, tm, t)
            enc.stdin.write(frame.tobytes())
            if progress and i % 30 == 0:
                progress(i / n, f"assembling {i}/{n} frames")
    finally:
        enc.stdin.close()
        enc.wait()
    log.info("assembled %s faceless video (%d shots, %.1fs)", fmt, len(shots), total)
