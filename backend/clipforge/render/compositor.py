"""Render one clip variant end to end."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .. import logs, settings
from ..config import paths
from ..media import ffmpeg_bin, probe, scene_cuts, video_encoder_args
from ..models import Moment, Source, Video
from . import audio as A
from . import edl as E
from . import faces as F
from . import plan as P
from .captions import CaptionTrack
from .overlays import H, W, Overlays, audiogram_frame, fit_frame
from .reframe import Camera, Rect

log = logs.get("render")

MAX_DECODE_H = 1440


@dataclass
class RenderSpec:
    hook_text: str
    hook_style: str = "bold_claim"
    caption_style: str = "clean"
    preset: str = "clean"         # clean | hype | minimal
    length: str = "full"          # full | tight
    opening: str = "straight"     # straight | cold_open | narrated
    layout: str = "auto"          # auto | follow | split | fit
    music_mood: str | None = None
    loop: bool = False


class FrameReader:
    """Sequential decoder at the output frame rate; `get(t)` must be called with non-decreasing t."""

    def __init__(self, path: str, start: float, fps: float, size: tuple[int, int]):
        self.w, self.h = size
        self.fps = fps
        self.start = start
        self.cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, start):.3f}", "-i", path,
                    "-an", "-vf", f"fps={fps},scale={self.w}:{self.h}:flags=bicubic", "-f", "rawvideo",
                    "-pix_fmt", "bgr24", "-"]
        # stderr is discarded: the decoder is killed mid-stream once its segment is done.
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=self.w * self.h * 3 * 2)
        self.n = 0
        self.cur: np.ndarray | None = None
        self.eof = False

    def _read(self) -> bool:
        size = self.w * self.h * 3
        buf = self.proc.stdout.read(size)
        if len(buf) < size:
            self.eof = True
            return False
        self.cur = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
        self.n += 1
        return True

    def get(self, t: float) -> np.ndarray:
        target = (t - self.start) * self.fps
        while not self.eof and (self.cur is None or self.n - 1 < target - 0.5):
            if not self._read():
                break
        if self.cur is None:
            return np.zeros((self.h, self.w, 3), np.uint8)
        return self.cur

    def close(self) -> None:
        try:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _decode_size(pw: int, ph: int) -> tuple[int, int]:
    if ph > MAX_DECODE_H:
        h = MAX_DECODE_H
        w = int(round(pw * h / ph / 2) * 2)
        return w, h
    return pw - pw % 2, ph - ph % 2


def _speech_intervals(words: list[dict], a: float, b: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for w in words:
        if w["e"] < a or w["s"] > b:
            continue
        if out and w["s"] - out[-1][1] < 0.35:
            out[-1][1] = w["e"]
        else:
            out.append([w["s"], w["e"]])
    return [(x, y) for x, y in out]


_LUT = None


def _grade(img: np.ndarray) -> None:
    global _LUT
    if _LUT is None:
        x = np.arange(256, dtype=np.float32) / 255
        curve = x - 0.22 * np.sin(2 * np.pi * x) / (2 * np.pi)      # gentle S-curve: a little more contrast
        _LUT = np.clip(curve * 255, 0, 255).astype(np.uint8)
    cv2.LUT(img, _LUT, dst=img)
    gray = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    cv2.addWeighted(img, 1.14, gray, -0.14, 0, dst=img)


def _sharpen(img: np.ndarray, amount: float) -> None:
    blur = cv2.GaussianBlur(img, (0, 0), 1.1)
    cv2.addWeighted(img, 1 + amount, blur, -amount, 0, dst=img)


def _warp(src: np.ndarray, r: Rect, out_w: int, out_h: int) -> np.ndarray:
    sx, sy = out_w / r.w, out_h / r.h
    M = np.array([[sx, 0, -r.x * sx], [0, sy, -r.y * sy]], dtype=np.float32)
    interp = cv2.INTER_CUBIC if sx > 1.2 else cv2.INTER_LINEAR
    return cv2.warpAffine(src, M, (out_w, out_h), flags=interp, borderMode=cv2.BORDER_REPLICATE)


def _cached_plan(moment: Moment, edl: E.EDL, spec: RenderSpec, src: Source, language: str) -> P.EditPlanModel | None:
    meta = dict(moment.meta or {})
    key = f"edit_plan:{spec.length}:{spec.opening}"
    if key in meta:
        try:
            return P.EditPlanModel.model_validate(meta[key])
        except Exception:  # noqa: BLE001
            pass
    model = P.ask_llm(edl.words, spec.hook_text, src.niche, language)
    if model is not None:
        from ..db import session

        with session() as s:
            m = s.get(Moment, moment.id)
            if m is not None:
                mm = dict(m.meta or {})
                mm[key] = model.model_dump()
                m.meta = mm
                s.add(m)
                s.commit()
    return model


# ---------------------------------------------------------------- master (slow, once per moment)

MS = 1.2                       # master is rendered at 1.2x output resolution so zooms stay sharp
MW, MH = int(W * MS), int(H * MS)
MARGIN = 3.0                   # seconds of extra footage either side (room for trims and cold opens)


@dataclass
class Master:
    path: str
    audio: str
    span0: float
    span1: float
    fps: float
    track: list                # every 3rd frame: [t_src, layout, focus_x, focus_y, face_bottom]
    face_ok: float | None

    def at(self, t_src: float) -> list:
        tr = self.track
        if not tr:
            return [t_src, "full", 0.5, 0.42, None]
        lo, hi = 0, len(tr) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if tr[mid][0] <= t_src:
                lo = mid
            else:
                hi = mid - 1
        return tr[lo]


def _master_key(moment: Moment, layout: str, a: float, b: float) -> str:
    return f"{layout}:{round(a, 1)}:{round(b, 1)}:v2"


def ensure_master(video: Video, moment: Moment, layout: str, need: tuple[float, float],
                  progress: Callable[[float, str], None]) -> Master:
    """Reuse a cached master covering `need` (source seconds) or build one."""
    from ..db import session

    for key, m in ((moment.meta or {}).get("masters") or {}).items():
        if key.startswith(f"{layout}:") and key.endswith(":v2") and m["span0"] <= need[0] + 1e-3 and m["span1"] >= need[1] - 1e-3 \
                and Path(m["path"]).exists() and Path(m["audio"]).exists():
            return Master(**m)
    dur = probe(video.file_path).duration or (need[1] + MARGIN)
    a, b = max(0.0, need[0] - MARGIN), min(dur, need[1] + MARGIN)
    master = build_master(video, moment, layout, a, b, progress)
    with session() as s:
        mm = s.get(Moment, moment.id)
        if mm is not None:
            meta = dict(mm.meta or {})
            masters = dict(meta.get("masters") or {})
            masters[_master_key(moment, layout, a, b)] = asdict(master)
            meta["masters"] = masters
            mm.meta = meta
            s.add(mm)
            s.commit()
            moment.meta = meta
    return master


def build_master(video: Video, moment: Moment, layout_pref: str, a: float, b: float,
                 progress: Callable[[float, str], None]) -> Master:
    t0 = time.time()
    transcript = json.loads(Path(video.transcript_path).read_text())
    words = transcript["words"]
    fps = float(settings.get("fps") or 30)
    pr = probe(video.file_path)
    audio_only = pr.width == 0 or pr.vcodec in ("mjpeg", "png", "")
    faceless = bool((moment.meta or {}).get("faceless"))
    out_dir = paths().work / str(video.id) / "masters"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"m{moment.id}_{layout_pref}_{int(a * 10)}_{int(b * 10)}"
    vpath, apath = out_dir / f"{stem}.mp4", out_dir / f"{stem}.wav"

    # Voice: cleaned once here; the assembly only cuts and mixes it.
    A.master_voice(video.file_path, a, b, apath, has_audio=pr.has_audio)

    dw, dh = (1080, 1080) if audio_only else _decode_size(pr.width, pr.height)
    camera = None
    if not audio_only and not faceless:
        progress(0.05, "tracking faces")
        cuts_rel = scene_cuts(video.file_path, a, b - a)
        analysis = F.analyze(video.file_path, a, b - a, cuts_rel)
        camera = Camera(dw, dh, analysis["tracks"], [a + c for c in cuts_rel], _speech_intervals(words, a, b), (a, b),
                        prefer=layout_pref)
    art = _load_art(video) if audio_only else None
    bars_energy = _energy_lookup(video) if audio_only else None
    grade_on = bool(settings.get("color_grade"))

    enc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{MW}x{MH}", "-r", f"{fps}", "-i", "-",
                            *video_encoder_args(settings.get("encoder") or "auto", "24M"), "-pix_fmt", "yuv420p",
                            "-g", f"{int(fps)}", str(vpath)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    reader = None if audio_only else FrameReader(video.file_path, a, fps, (dw, dh))
    n = int(round((b - a) * fps))
    track: list = []
    face_ok = face_total = 0
    try:
        for i in range(n):
            t_src = a + i / fps
            focus, face_bottom = (0.5, 0.42), None
            if audio_only:
                frame = cv2.resize(audiogram_frame(art, 0, t_src, _bars(bars_energy, t_src)), (MW, MH))
                layout = "audiogram"
            else:
                src_frame = reader.get(t_src)  # type: ignore[union-attr]
                layout, rects = ("full", [Rect(0, 0, dw, dh)]) if camera is None else camera.rects(t_src, 1.0)
                if layout == "split":
                    frame = np.vstack([_warp(src_frame, rects[0], MW, MH // 2), _warp(src_frame, rects[1], MW, MH - MH // 2)])
                    frame[MH // 2 - 4:MH // 2 + 4] = (20, 20, 20)
                    upscale = MW / rects[0].w / MS
                elif layout == "fit":
                    frame = fit_frame(src_frame, MW, MH)
                    upscale = W / src_frame.shape[1]
                elif layout == "screencast":
                    frame = fit_frame(src_frame, MW, MH, y_center=0.36)
                    _paste_bubble(frame, _warp(src_frame, rects[1], int(460 * MS), int(460 * MS)), MW // 2, int(MH * 0.68))
                    upscale = W / src_frame.shape[1]
                else:
                    r = rects[0]
                    frame = _warp(src_frame, r, MW, MH)
                    upscale = W / r.w
                    sh = camera.shot_at(t_src) if camera is not None else None
                    if layout == "follow" and sh is not None and sh.holds:
                        tid = next((tid for s, e, tid in sh.holds if s <= t_src < e), None)
                        if tid is not None:
                            fx, fy, fhh = camera._face_at(tid, t_src)
                            focus = ((fx - r.x) / r.w, (fy - r.y) / r.h)
                            face_bottom = (fy + fhh * 0.62 - r.y) / r.h
                            face_total += 1
                            if r.x <= fx - fhh * 0.3 and fx + fhh * 0.3 <= r.x + r.w:
                                face_ok += 1
                if grade_on:
                    _grade(frame)
                if upscale > 1.25:
                    _sharpen(frame, 0.35 if upscale < 1.8 else 0.5)
            if i % 3 == 0:
                track.append([round(t_src, 3), layout, round(float(focus[0]), 3), round(float(focus[1]), 3),
                              None if face_bottom is None else round(float(face_bottom), 3)])
            enc.stdin.write(frame.tobytes())
            if i % 20 == 0:
                progress(0.08 + 0.32 * i / max(1, n), f"framing the speaker {i}/{n}")
    finally:
        if reader:
            reader.close()
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"master encode failed: {err[-1200:]}")
    log.info("master %s (%.0fs of footage) built in %.1fs", vpath.name, b - a, time.time() - t0)
    return Master(path=str(vpath), audio=str(apath), span0=a, span1=b, fps=fps, track=track,
                  face_ok=(face_ok / face_total) if face_total else None)


# ---------------------------------------------------------------- assembly (fast, every change)

def _zoom_crop(frame: np.ndarray, z: float, fx: float, fy: float, out_w: int, out_h: int) -> np.ndarray:
    """Crop the master around the speaker for zoom z (1 = whole master) and resize to the output."""
    fh, fw = frame.shape[:2]
    z = max(1.0, z)
    cw, ch = fw / z, fh / z
    pull = min(1.0, (z - 1.0) * 3)                 # the further in, the more we centre on the face
    cx = fw * (0.5 + (fx - 0.5) * pull)
    cy = fh * (0.5 + (fy + 0.08 - 0.5) * pull)
    r = Rect(min(max(0.0, cx - cw / 2), fw - cw), min(max(0.0, cy - ch / 2), fh - ch), cw, ch)
    return _warp(frame, r, out_w, out_h)


def render(video: Video, src: Source, moment: Moment, spec: RenderSpec, out_path: Path,
           caption_edits: dict | None = None, progress: Callable[[float, str], None] | None = None,
           burn: bool = True, overrides: dict | None = None, quality: str | None = None) -> dict:
    """Build (or reuse) the moment's master, then assemble this variant from it.

    burn=False: a clean draft for review (text is drawn live by the dashboard).
    burn=True: the final file with text baked in (used on approval)."""
    t_start = time.time()
    progress = progress or (lambda f, m="": None)
    quality = quality or ("final" if burn else "draft")
    transcript = json.loads(Path(video.transcript_path).read_text())
    words = transcript["words"]

    start, end = moment.start_s, moment.end_s
    if spec.length == "tight" and moment.tight_start_s is not None:
        start, end = moment.tight_start_s, moment.tight_end_s or end
    cold = tuple(moment.punchline_s) if spec.opening == "cold_open" and moment.punchline_s else None
    edl = E.build(words, start, end, jump_cuts=bool(settings.get("jump_cuts")),
                  max_pause=float(settings.get("max_pause_s")), cold_open=cold, alt_zoom=1.0,
                  exclude=(moment.meta or {}).get("drop") or None)
    tmp = paths().tmp / f"render_{out_path.stem}_{quality}"
    tmp.mkdir(parents=True, exist_ok=True)
    narration = None
    if spec.opening == "narrated" and spec.hook_text:
        try:
            narration = tmp / "narration.wav"
            nd = A.tts(spec.hook_text, narration, settings.get("tts_voice") or "")
            edl = E.prepend_still(edl, nd + 0.25, edl.segments[0].src_start, words)
        except Exception as exc:  # noqa: BLE001
            log.warning("narration failed, rendering without it: %s", exc)
            narration = None
    duration = edl.duration
    timed = [s for s in edl.segments if s.kind != "still"] or edl.segments
    need = (min(s.src_start for s in timed), max(s.src_end for s in timed))
    master = ensure_master(video, moment, spec.layout or "auto", need, progress)

    progress(0.42, "planning edit")
    model = _cached_plan(moment, edl, spec, src, video.language or "en")
    hook_s = float(settings.get("hook_seconds"))
    try:
        stress_list = json.loads(Path(video.timeline_path).read_text()).get("stress") or []
    except (OSError, ValueError):
        stress_list = []
    tl = P.schedule(edl, model, emphasis_words=moment.emphasis or [], emoji_map=moment.emoji or {},
                    hook_seconds=hook_s, stress={i: s for i, s in enumerate(stress_list) if s}, preset=spec.preset,
                    punch_zooms=bool(settings.get("punch_zooms")), sfx=bool(settings.get("sfx")),
                    headline_fallback=moment.title, cta_fallback="Follow for part 2" if spec.loop is False else "")
    if spec.loop:
        tl.loop = True
    faceless = bool((moment.meta or {}).get("faceless"))
    if faceless:
        tl.framings, tl.punches, tl.push_in = [(0.0, 1.0)], [], 0.0
    broll_frames: list = []
    if tl.broll and int(settings.get("ai_broll") or 0) and not faceless:
        tl.broll = tl.broll[: int(settings.get("ai_broll"))]
        broll_frames = _broll_images(video, moment, tl.broll, progress)

    progress(0.48, "mixing audio")
    mood = spec.music_mood if spec.music_mood is not None else tl.music_mood
    if faceless and (not mood or mood == "none"):
        mood = (moment.meta or {}).get("music_mood") or "inspiring"
    music_on = settings.get("music_enabled") or (faceless and settings.get("faceless_music"))
    music = A.pick_music(mood, moment.id or 0) if music_on and mood != "none" else None
    audio_path = tmp / "audio.m4a"
    A.build(master.audio, edl, audio_path, sfx_events=tl.sfx, music=music,
            music_db=float(settings.get("music_volume_db")), narration=narration,
            target_lufs=float(settings.get("target_lufs")), has_audio=True, src_offset=master.span0,
            voice_chain=False)

    from .burn import merged

    text = merged({"caption_style": spec.caption_style, "brand": settings.get("brand_color")}, overrides)
    caps = CaptionTrack(edl.words, text.get("caption_style") or spec.caption_style, text.get("brand") or settings.get("brand_color"),
                        tl.emphasis, font_override=settings.get("caption_font") or "",
                        edits=(text.get("caption_edits") or caption_edits),
                        size_scale=float(text.get("caption_scale") or 1.0))
    emoji = tl.emoji if text.get("emoji") is not False else []
    over = Overlays(hook=text.get("hook") if text.get("hook") is not None else spec.hook_text,
                    headline=text.get("headline") if text.get("headline") is not None else tl.headline,
                    credit=src.credit_text, credit_link=src.credit_link,
                    cta=text.get("cta") if text.get("cta") is not None else tl.cta, brand=text.get("brand") or settings.get("brand_color"),
                    duration=duration, hook_seconds=hook_s,
                    progress_bar=bool(text.get("progress_bar", settings.get("progress_bar"))),
                    end_card=bool(text.get("end_card", bool(settings.get("end_card")) and not tl.loop)),
                    cards=tl.cards, emoji=emoji, flashes=tl.flashes,
                    cta_keyword=(src.cta_keyword if getattr(src, "cta_keyword", "") else ""),
                    key_words=set(moment.emphasis or []) | {"quit", "stop", "never", "secret", "truth", "mistake"})

    fps = master.fps
    final = quality == "final"
    dec_size = (MW, MH)                               # full master res so zooms stay sharp
    enc = subprocess.Popen([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{W}x{H}", "-r", f"{fps}", "-i", "-", "-i", str(audio_path), "-map", "0:v", "-map", "1:a",
                            *video_encoder_args(settings.get("encoder") or "auto",
                                                settings.get("video_bitrate") or "10M"),
                            "-pix_fmt", "yuv420p", "-r", f"{fps}", "-g", f"{int(fps * 2)}", "-c:a", "copy",
                            "-movflags", "+faststart", "-shortest", str(out_path)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n_frames = int(round(duration * fps))
    readers: dict[int, FrameReader] = {}
    still_cache: dict[int, np.ndarray] = {}
    layouts: dict[str, int] = {}
    thumb_frame = None
    clean_thumb = None
    thumb_t = min(duration * 0.5, hook_s * 0.6)
    cap_y_smooth: float | None = None
    y_track: list = []
    last_caption_y = caps.y
    try:
        for i in range(n_frames):
            t = i / fps
            seg = edl.segment_at(t)
            si = edl.segments.index(seg)
            t_src = edl.to_source(t)
            m_t = t_src - master.span0
            zoom = seg.zoom * tl.zoom_at(t)
            if seg.kind == "still":
                zoom *= 1.0 + 0.06 * (t - seg.out_start) / max(0.1, seg.duration)
                if si not in still_cache:
                    rd = FrameReader(master.path, m_t, fps, dec_size)
                    still_cache[si] = rd.get(m_t).copy()
                    rd.close()
                mframe = still_cache[si]
            else:
                if si not in readers:
                    prev = edl.segments[si - 1] if si > 0 else None
                    if prev is not None and prev.kind == seg.kind == "main" and (si - 1) in readers \
                            and seg.src_start >= prev.src_end - 0.01:
                        readers[si] = readers.pop(si - 1)
                    else:
                        for k in list(readers):
                            readers.pop(k).close()
                        readers[si] = FrameReader(master.path, m_t, fps, dec_size)
                mframe = readers[si].get(m_t)
            _, layout, fx, fy, face_bottom = master.at(t_src)
            panel = over.panel_at(t)
            if panel is not None:
                from .graphics import PANEL_H, draw_panel

                fh, fw = mframe.shape[:2]
                bh = H - PANEL_H
                sub_w = fw
                sub_h = sub_w * bh / W
                cy = fh * max(0.0, min(1.0, fy + 0.05))
                sub = Rect(0, min(max(0.0, cy - sub_h / 2), fh - sub_h), sub_w, sub_h)
                frame = np.empty((H, W, 3), np.uint8)
                frame[PANEL_H:] = _warp(mframe, sub, W, bh)
                draw_panel(frame, panel, t)
                layout = "explainer"
            else:
                frame = _zoom_crop(mframe, min(zoom, 1.6), fx, fy, W, H) if layout in ("follow", "full", "group") \
                    else _zoom_crop(mframe, 1.0, 0.5, 0.5, W, H)
            layouts[layout] = layouts.get(layout, 0) + 1
            for ev, img in broll_frames:
                if ev.start <= t <= ev.end:
                    f = (t - ev.start) / max(0.1, ev.end - ev.start)
                    a_ = min(1.0, (t - ev.start) / 0.12, (ev.end - t) / 0.12)
                    cv2.addWeighted(_broll_frame(img, f), a_, frame, 1 - a_, 0, dst=frame)
                    layout = "broll"
            over.draw_card_background(frame, t)
            cap_y = (0.5 if layout == "split" else 0.71 if layout in ("fit", "audiogram")
                     else 0.84 if layout in ("explainer", "screencast") else caps.y)
            if layout == "follow" and face_bottom is not None and cap_y - 0.04 < face_bottom:
                target = min(0.79, face_bottom + 0.06)
                cap_y_smooth = target if cap_y_smooth is None else cap_y_smooth + (target - cap_y_smooth) * 0.15
                cap_y = cap_y_smooth
            if text.get("caption_y") is not None:
                cap_y = float(text["caption_y"])
            last_caption_y = cap_y
            if i % 3 == 0:
                y_track.append([round(t, 2), round(cap_y, 3), layout])
            over.draw(frame, t, cap_y, layer="base")
            if burn:
                if not text.get("hide_captions"):
                    caps.draw(frame, t, cap_y)
                over.draw(frame, t, cap_y, layer="text")
            if thumb_frame is None and t >= thumb_t:
                thumb_frame = frame.copy()
                if not burn:
                    clean_thumb = frame.copy()      # poster for the live preview (text is drawn on top there)
                    caps.draw(thumb_frame, t, cap_y)
                    over.draw(thumb_frame, t, cap_y, layer="text")
            enc.stdin.write(frame.tobytes())
            if i % 15 == 0:
                progress(0.5 + 0.49 * i / max(1, n_frames), f"assembling {i}/{n_frames} frames")
    finally:
        for rd in readers.values():
            rd.close()
        try:
            enc.stdin.close()
        except BrokenPipeError:
            pass
        err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
        rc = enc.wait()
    if rc != 0:
        raise RuntimeError(f"encoder failed: {err[-1500:]}")

    thumb_path = out_path.with_suffix(".jpg")
    if thumb_frame is not None:
        cv2.imwrite(str(thumb_path), cv2.resize(thumb_frame, (540, 960), interpolation=cv2.INTER_AREA),
                    [cv2.IMWRITE_JPEG_QUALITY, 88])
    clean_path = out_path.with_suffix(".clean.jpg")
    if clean_thumb is not None:
        cv2.imwrite(str(clean_path), cv2.resize(clean_thumb, (540, 960), interpolation=cv2.INTER_AREA),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    for p in tmp.iterdir():
        p.unlink(missing_ok=True)
    tmp.rmdir()
    elapsed = time.time() - t_start
    log.info("assembled %s (%s, %.1fs) in %.1fs", out_path.name, quality, duration, elapsed)
    return {
        "duration": duration, "frames": n_frames, "edl": edl.to_json(), "layouts": layouts,
        "face_in_frame": master.face_ok,
        "words": [[w.index, w.text, round(w.start, 3), round(w.end, 3), w.prob] for w in edl.words],
        "first_word_at": edl.words[0].start if edl.words else None,
        "hook_until": hook_s if spec.hook_text else 0, "boundaries": edl.boundaries(),
        "zooms": len(tl.punches), "framings": len(tl.framings), "preset": tl.preset, "broll": len(broll_frames),
        "emphasis_words": [edl.words[i].text for i in sorted(tl.emphasis)],
        "cards": [asdict(c) for c in tl.cards], "emoji": len(tl.emoji), "sfx": len(tl.sfx),
        "headline": tl.headline, "cta": tl.cta, "music": music.name if music else "", "loop": tl.loop,
        "thumb": str(thumb_path) if thumb_frame is not None else "",
        "thumb_clean": str(clean_path) if clean_thumb is not None else "", "render_seconds": round(elapsed, 1),
        "caption_y": last_caption_y, "spec": asdict(spec), "burned": burn, "quality": quality,
        "overlay": {
            "duration": duration, "fps": fps, "brand": settings.get("brand_color"),
            "caption_style": spec.caption_style, "caption_font": settings.get("caption_font") or "",
            "emphasis": sorted(tl.emphasis), "caption_y_track": y_track,
            "hook": spec.hook_text, "hook_seconds": hook_s if spec.hook_text else 0,
            "headline": tl.headline, "credit": src.credit_text, "credit_link": src.credit_link,
            "cta": tl.cta, "cta_keyword": getattr(src, "cta_keyword", "") or "",
            "progress_bar": bool(settings.get("progress_bar")),
            "end_card": bool(settings.get("end_card")) and not tl.loop,
            "emoji": [[e.start, e.emoji] for e in tl.emoji],
            "panels": [[c.start, c.end] for c in tl.cards if c.panel],
            "key_words": sorted(set(moment.emphasis or [])),
        },
    }


def _broll_images(video: Video, moment: Moment, events: list, progress) -> list:
    """Draw B-roll stills with the local image model (cached per moment so every variant reuses them)."""
    work = paths().work / str(video.id) / "broll"
    work.mkdir(parents=True, exist_ok=True)
    style = settings.get("broll_style") or "cinematic film still, dramatic natural light, shallow depth of field, rich color"
    todo, out = [], []
    for k, ev in enumerate(events):
        import hashlib

        path = work / f"m{moment.id}_{k}_{hashlib.md5(ev.visual.encode()).hexdigest()[:10]}.png"
        ev.image = str(path)
        if not path.exists():
            todo.append((ev, path))
    if todo:
        progress(0.08, f"drawing {len(todo)} b-roll shot(s)")
        from .. import llm
        from ..faceless import draw_scenes

        llm.unload()                       # free RAM for the image model
        tmp = work / "tmp"
        tmp.mkdir(exist_ok=True)
        try:
            imgs = draw_scenes([f"{ev.visual}. {style}. vertical composition, no people, no faces, no hands, no text, "
                                f"no watermark, no logos"
                                for ev, _ in todo], tmp, seed=moment.id or 7, progress=lambda f, m: None)
            for (ev, path), img in zip(todo, imgs):
                img.rename(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("b-roll generation failed: %s", exc)
    for ev in events:
        if ev.image and Path(ev.image).exists():
            img = cv2.imread(ev.image)
            if img is not None and float(img.mean()) < 45:
                log.info("dropping a b-roll shot that came out too dark")
                Path(ev.image).unlink(missing_ok=True)
                img = None
            if img is not None:
                out.append((ev, cv2.resize(img, (int(W * 1.12), int(H * 1.12)), interpolation=cv2.INTER_CUBIC)))
    return out


def _broll_frame(base: np.ndarray, f: float) -> np.ndarray:
    """Slow push-in across the cutaway."""
    z = 1.0 + 0.08 * f
    bh, bw = base.shape[:2]
    cw, ch = bw / z / 1.0, bh / z / 1.0
    cw, ch = min(cw, bw), min(ch, bh)
    x0, y0 = (bw - cw) / 2, (bh - ch) / 2
    M = np.array([[W / cw, 0, -x0 * W / cw], [0, H / ch, -y0 * H / ch]], np.float32)
    return cv2.warpAffine(base, M, (W, H), flags=cv2.INTER_LINEAR)


def _paste_bubble(frame: np.ndarray, img: np.ndarray, cx: int, cy: int, ring: int = 8) -> None:
    """Circular presenter bubble with a white ring and soft shadow."""
    d = img.shape[0]
    r = d // 2
    y0, x0 = cy - r, cx - r
    shadow = np.zeros(frame.shape[:2], np.uint8)
    cv2.circle(shadow, (cx, cy + 10), r + ring + 6, 255, -1, lineType=cv2.LINE_AA)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 14).astype(np.float32)[..., None] / 255 * 0.45
    frame[:] = (frame.astype(np.float32) * (1 - shadow)).astype(np.uint8)
    cv2.circle(frame, (cx, cy), r + ring, (255, 255, 255), -1, lineType=cv2.LINE_AA)
    mask = np.zeros((d, d), np.uint8)
    cv2.circle(mask, (r, r), r, 255, -1, lineType=cv2.LINE_AA)
    m = mask.astype(np.float32)[..., None] / 255
    region = frame[y0:y0 + d, x0:x0 + d].astype(np.float32)
    frame[y0:y0 + d, x0:x0 + d] = (img * m + region * (1 - m)).astype(np.uint8)


def _load_art(video: Video) -> np.ndarray | None:
    if not video.thumbnail:
        return None
    try:
        import httpx

        if video.thumbnail.startswith("http"):
            data = httpx.get(video.thumbnail, timeout=20, follow_redirects=True).content
            arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        else:
            arr = cv2.imread(video.thumbnail)
        return arr
    except Exception:  # noqa: BLE001
        return None


def _energy_lookup(video: Video):
    try:
        tl = json.loads(Path(video.timeline_path).read_text())
        e = np.array(tl["energy"], dtype=np.float32)
        lo, hi = np.percentile(e, 20), np.percentile(e, 99)
        return (np.clip((e - lo) / max(1e-3, hi - lo), 0, 1), tl.get("hop", 0.1))
    except Exception:  # noqa: BLE001
        return None


def _bars(energy, t: float, n: int = 24) -> np.ndarray:
    if energy is None:
        return np.zeros(n)
    e, hop = energy
    i = int(t / hop)
    seg = e[max(0, i - n // 2): i + n // 2]
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    return seg[:n]
