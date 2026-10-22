"""Smart 9:16 reframing.

Per shot (between hard cuts) we pick a layout:
  follow  - one person fills the frame; the crop tracks their face smoothly
  group   - two people close together share one crop
  split   - two people far apart, trading lines: stacked top/bottom
  fit     - nobody detected (screen share, b-roll): full frame over a blurred fill
When the active speaker changes inside a wide shot we cut, like a multicam
edit, rather than whip-panning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .faces import Track, mouth_activity


@dataclass
class Shot:
    start: float
    end: float
    layout: str = "fit"
    # Sample-rate keyframes: (time, track_id) of who the camera is on.
    holds: list[tuple[float, float, int]] = field(default_factory=list)   # (start, end, track id)
    pair: tuple[int, int] | None = None   # split / group tracks (left, right)


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float


class Camera:
    def __init__(self, src_w: int, src_h: int, tracks: list[Track], cuts: list[float],
                 speech: list[tuple[float, float]], span: tuple[float, float], prefer: str = "auto"):
        self.W, self.H = src_w, src_h
        self.tracks = {t.id: t for t in tracks}
        self.speech = speech
        self.prefer = prefer
        aspect = src_w / src_h
        if aspect > 9 / 16:
            self.base_h, self.base_w = src_h, src_h * 9 / 16
        else:
            self.base_w, self.base_h = src_w, src_w * 16 / 9
        a, b = span
        bounds = [a] + [c for c in sorted(cuts) if a < c < b] + [b]
        self.shots = [self._plan_shot(s, e) for s, e in zip(bounds, bounds[1:])]
        self._smooth_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------ planning

    def _speaking(self, t: float) -> bool:
        return any(s - 0.15 <= t <= e + 0.15 for s, e in self.speech)

    def _visible(self, s: float, e: float) -> list[Track]:
        out = []
        n_expected = max(1, (e - s) * 5)
        for tr in self.tracks.values():
            n = sum(1 for t in tr.times if s <= t <= e)
            if n >= max(2, 0.35 * n_expected):
                out.append(tr)
        return sorted(out, key=lambda tr: -tr.size)[:3]

    def _plan_shot(self, s: float, e: float) -> Shot:
        shot = Shot(s, e)
        if self.W / self.H <= 9 / 16 + 0.02:
            shot.layout = "full"
            return shot
        vis = self._visible(s, e)
        if not vis or self.prefer == "fit":
            shot.layout = "fit"
            return shot
        if len(vis) == 1:
            f = vis[0]
            fx = float(np.median([x.x for x in f.faces]))
            fy = float(np.median([x.y for x in f.faces]))
            # A small face near a corner over a big picture = webcam over a screen recording.
            if f.size < 0.16 and (fx < 0.3 or fx > 0.7) and (fy < 0.35 or fy > 0.65) and self.prefer in ("auto", "screencast"):
                shot.layout = "screencast"
                shot.holds = [(s, e, f.id)]
                return shot
            shot.layout = "follow"
            shot.holds = [(s, e, vis[0].id)]
            return shot

        a, b = sorted(vis[:2], key=lambda tr: np.median([f.x for f in tr.faces]))
        ax = float(np.median([f.x for f in a.faces])) * self.W
        bx = float(np.median([f.x for f in b.faces])) * self.W
        close = abs(bx - ax) + max(a.size, b.size) * self.H * 0.9 < self.base_w * 0.95

        # Who is talking at each sample.
        times = np.arange(s, e, 0.2)
        who = []
        for t in times:
            if not self._speaking(t):
                who.append(None)
                continue
            sa, sb = mouth_activity(a, t), mouth_activity(b, t)
            if max(sa, sb) < 0.004:
                who.append(None)
            else:
                who.append(a.id if sa >= sb * 1.15 else b.id if sb >= sa * 1.15 else None)
        holds = _hysteresis(times, who, default=a.id if a.size >= b.size else b.id, min_hold=1.4, confirm=0.8)
        switches = max(0, len(holds) - 1)
        rate = switches / max(1.0, (e - s) / 10)

        if close:
            shot.layout, shot.pair = "group", (a.id, b.id)
        elif self.prefer == "split" or (self.prefer == "auto" and (rate >= 2.0 or (switches == 0 and _ambiguous(who)))):
            shot.layout, shot.pair = "split", (a.id, b.id)
        else:
            shot.layout = "follow"
            shot.holds = holds or [(s, e, a.id)]
        return shot

    # ------------------------------------------------------------ per-frame

    def shot_at(self, t: float) -> Shot:
        for sh in self.shots:
            if sh.start <= t < sh.end:
                return sh
        return self.shots[-1]

    def _smoothed(self, track_id: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Track centre over time with jitter removed (dead zone + exponential smoothing)."""
        if track_id in self._smooth_cache:
            return self._smooth_cache[track_id]
        tr = self.tracks[track_id]
        ts = np.array(tr.times)
        xs = np.array([f.x * self.W for f in tr.faces])
        ys = np.array([f.y * self.H for f in tr.faces])
        hs = np.array([f.h * self.H for f in tr.faces])
        out_x = np.empty_like(xs)
        dead = self.base_w * 0.05
        cur = xs[0]
        for i, x in enumerate(xs):
            if abs(x - cur) > dead:
                cur = cur + (x - cur) * 0.35
            out_x[i] = cur
        k = np.ones(5) / 5
        if len(ys) >= 5:
            ys = np.convolve(np.pad(ys, 2, mode="edge"), k, mode="valid")
            hs = np.convolve(np.pad(hs, 2, mode="edge"), k, mode="valid")
        self._smooth_cache[track_id] = (ts, out_x, np.stack([ys, hs]))
        return self._smooth_cache[track_id]

    def _face_at(self, track_id: int, t: float) -> tuple[float, float, float]:
        ts, xs, yh = self._smoothed(track_id)
        x = float(np.interp(t, ts, xs))
        y = float(np.interp(t, ts, yh[0]))
        h = float(np.interp(t, ts, yh[1]))
        return x, y, h

    def _auto_zoom(self, face_h: float, target: float) -> float:
        """Zoom so the face fills `target` of the frame height, without upscaling past ~2.4x."""
        if face_h <= 0:
            return 1.0
        max_zoom = max(1.0, self.base_w / (1080 / 2.4))
        return float(min(max_zoom, max(1.0, target * self.base_h / face_h)))

    def _crop(self, cx: float, cy: float, w: float, h: float) -> Rect:
        w, h = min(w, self.W), min(h, self.H)
        x = min(max(0.0, cx - w / 2), self.W - w)
        y = min(max(0.0, cy - h / 2), self.H - h)
        return Rect(x, y, w, h)

    def rects(self, t: float, zoom: float = 1.0) -> tuple[str, list[Rect]]:
        sh = self.shot_at(t)
        if sh.layout == "screencast" and sh.holds:
            fx, fy, fh = self._face_at(sh.holds[0][2], t)
            side = min(self.H, fh * 2.2)
            return "screencast", [Rect(0, 0, self.W, self.H), self._crop(fx, fy, side, side)]
        if sh.layout in ("fit", "full"):
            if sh.layout == "full" and zoom > 1.001:
                w, h = self.W / zoom, self.H / zoom
                return "full", [self._crop(self.W / 2, self.H * 0.45, w, h)]
            return sh.layout, [Rect(0, 0, self.W, self.H)]

        if sh.layout == "split" and sh.pair:
            out = []
            for k, tid in enumerate(sh.pair):
                fx, fy, fh = self._face_at(tid, t)
                ch = min(self.H, max(self.H * 0.42, fh * 2.7)) / zoom
                cw = ch * 9 / 8
                # Top speaker sits lower in their half to leave room for the hook and headline.
                out.append(self._crop(fx, fy - ch * 0.10 if k == 0 else fy + ch * 0.06, cw, ch))
            return "split", out

        if sh.layout == "group" and sh.pair:
            (ax, ay, ah), (bx, by, bh) = (self._face_at(i, t) for i in sh.pair)
            cx, fy, fh = (ax + bx) / 2, (ay + by) / 2, max(ah, bh)
            base = self._auto_zoom(float(np.median([self.tracks[i].size for i in sh.pair])) * self.H, 0.18)
        else:
            tid = next((tid for s, e, tid in sh.holds if s <= t < e), sh.holds[-1][2] if sh.holds else None)
            if tid is None:
                return "fit", [Rect(0, 0, self.W, self.H)]
            cx, fy, fh = self._face_at(tid, t)
            base = self._auto_zoom(self.tracks[tid].size * self.H, 0.25)
        # Cap total upscaling at ~2.8x and leave headroom so punch-ins still read.
        cap = max(1.0, self.base_w / (1080 / 2.8))
        # Never so tight the face fills more than ~42% of the frame height (no cut-off chins/foreheads).
        if fh > 0:
            # (Vision's box covers eyebrows-to-chin; the whole head is ~1.5x taller.)
            cap = min(cap, max(1.0, 0.30 * self.base_h / fh))
        zoom = min(cap, zoom * min(base, cap / 1.25))
        w, h = self.base_w / zoom, self.base_h / zoom
        # Keep eyes around 38% from the top when zoomed in.
        cy = fy + h * 0.12 if h < self.H else self.H / 2
        return "follow", [self._crop(cx, cy, w, h)]


def _ambiguous(who: list) -> bool:
    known = [w for w in who if w is not None]
    return len(known) < 0.3 * max(1, len(who))


def _hysteresis(times: np.ndarray, who: list, default: int, min_hold: float, confirm: float) -> list[tuple[float, float, int]]:
    if len(times) == 0:
        return []
    cur = next((w for w in who if w is not None), default)
    holds: list[list] = [[float(times[0]), float(times[-1]) + 0.2, cur]]
    cand, cand_since = None, None
    for t, w in zip(times, who):
        if w is None or w == cur:
            cand, cand_since = None, None
            continue
        if w != cand:
            cand, cand_since = w, t
        if t - cand_since >= confirm and t - holds[-1][0] >= min_hold:
            # Cut to the new speaker a beat before they were confirmed.
            switch = max(holds[-1][0] + min_hold, cand_since - 0.2)
            holds[-1][1] = switch
            holds.append([switch, float(times[-1]) + 0.2, w])
            cur, cand, cand_since = w, None, None
    return [(a, b, c) for a, b, c in holds]
