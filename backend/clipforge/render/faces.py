"""Face detection, tracking and active-speaker estimation.

Detection uses Apple's Vision framework (Neural Engine, no model download) and
falls back to OpenCV's Haar cascade elsewhere. Faces are linked into tracks
across sampled frames; the active speaker is the track whose mouth moves most
while someone is talking.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np

from .. import logs
from ..media import ffmpeg_bin

log = logs.get("faces")


@dataclass
class Face:
    x: float        # centre, normalised 0..1 (left -> right)
    y: float        # centre, normalised 0..1 (top -> bottom)
    w: float        # normalised width
    h: float        # normalised height
    mouth: float = 0.0   # mouth opening relative to face height (0 when unknown)
    conf: float = 1.0


@dataclass
class Track:
    id: int
    times: list[float] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)

    def at(self, t: float, tol: float = 0.6) -> Face | None:
        if not self.times:
            return None
        i = int(np.searchsorted(self.times, t))
        best, bd = None, tol
        for j in (i - 1, i):
            if 0 <= j < len(self.times) and abs(self.times[j] - t) <= bd:
                best, bd = self.faces[j], abs(self.times[j] - t)
        return best

    @property
    def size(self) -> float:
        return float(np.median([f.h for f in self.faces])) if self.faces else 0.0


# ---------------------------------------------------------------- detectors

class VisionDetector:
    def __init__(self):
        import Quartz  # noqa: F401
        import Vision  # noqa: F401

    def __call__(self, frame_bgr: np.ndarray) -> list[Face]:
        import Quartz
        import Vision
        from Foundation import NSData

        h, w = frame_bgr.shape[:2]
        rgba = np.ascontiguousarray(np.dstack([frame_bgr[..., ::-1], np.full((h, w), 255, np.uint8)]))
        data = NSData.dataWithBytes_length_(rgba.tobytes(), rgba.nbytes)
        provider = Quartz.CGDataProviderCreateWithCFData(data)
        img = Quartz.CGImageCreate(w, h, 8, 32, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                   Quartz.kCGImageAlphaNoneSkipLast, provider, None, False,
                                   Quartz.kCGRenderingIntentDefault)
        req = Vision.VNDetectFaceLandmarksRequest.alloc().init()
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, {})
        ok, _err = handler.performRequests_error_([req], None)
        if not ok:
            return []
        faces = []
        for obs in req.results() or []:
            bb = obs.boundingBox()
            bx, by, bw, bh = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
            mouth = 0.0
            lm = obs.landmarks()
            if lm is not None and lm.innerLips() is not None:
                region = lm.innerLips()
                n = region.pointCount()
                pts = region.normalizedPoints()
                ys = [pts[i].y for i in range(n)]
                xs = [pts[i].x for i in range(n)]
                if ys and xs:
                    mouth = (max(ys) - min(ys)) / max(1e-3, (max(xs) - min(xs)) + 0.25)
            faces.append(Face(x=bx + bw / 2, y=1 - (by + bh / 2), w=bw, h=bh, mouth=float(mouth),
                              conf=float(obs.confidence())))
        return faces


class HaarDetector:
    """OpenCV fallback for non-Mac systems (Haar cascades left core OpenCV in 5.x)."""

    def __init__(self):
        import cv2

        self.cascade = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            self.cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        else:
            log.warning("no face detector available; clips will use the full-frame layout")

    def __call__(self, frame_bgr: np.ndarray) -> list[Face]:
        import cv2

        if self.cascade is None:
            return []
        h, w = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        rects = self.cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(int(h * 0.08),) * 2)
        return [Face(x=(x + fw / 2) / w, y=(y + fh / 2) / h, w=fw / w, h=fh / h) for x, y, fw, fh in rects]


_detector = None


def detector():
    global _detector
    if _detector is None:
        if sys.platform == "darwin":
            try:
                _detector = VisionDetector()
                return _detector
            except Exception as exc:  # noqa: BLE001
                log.info("Apple Vision unavailable (%s); using OpenCV", exc)
        _detector = HaarDetector()
    return _detector


# ---------------------------------------------------------------- sampling

def sample_frames(path: str, start: float, duration: float, fps: float = 5.0, width: int = 640):
    """Yield (time, frame) at `fps` across [start, start+duration] from a small proxy decode."""
    import json

    probe = subprocess.run([ffmpeg_bin().replace("ffmpeg", "ffprobe"), "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "json", path],
                           capture_output=True, text=True)
    st = json.loads(probe.stdout or "{}").get("streams", [{}])[0]
    sw, sh = int(st.get("width", 1920)), int(st.get("height", 1080))
    height = int(round(width * sh / sw / 2) * 2)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-i", path, "-an", "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = width * height * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()


# ---------------------------------------------------------------- tracking

def _match(a: Face, b: Face) -> float:
    d = np.hypot(a.x - b.x, (a.y - b.y) * 0.6)
    return d / max(a.w, b.w, 1e-3)


def build_tracks(samples: list[tuple[float, list[Face]]], cuts: list[float], max_gap: float = 1.2) -> list[Track]:
    tracks: list[Track] = []
    active: list[Track] = []
    cut_iter = sorted(cuts)
    next_id = 0
    ci = 0
    for t, faces in samples:
        while ci < len(cut_iter) and cut_iter[ci] <= t:
            active = []          # a hard cut ends every track
            ci += 1
        active = [tr for tr in active if t - tr.times[-1] <= max_gap]
        used = set()
        for f in sorted(faces, key=lambda f: -f.h):
            best, bd = None, 0.9
            for tr in active:
                if id(tr) in used:
                    continue
                d = _match(tr.faces[-1], f)
                if d < bd:
                    best, bd = tr, d
            if best is None:
                best = Track(next_id)
                next_id += 1
                tracks.append(best)
                active.append(best)
            best.times.append(t)
            best.faces.append(f)
            used.add(id(best))
    # Drop flickers (false positives that appear for one or two samples).
    return [tr for tr in tracks if len(tr.times) >= 3 and tr.size > 0.05]


def mouth_activity(track: Track, t: float, window: float = 0.7) -> float:
    vals = [f.mouth for tt, f in zip(track.times, track.faces) if abs(tt - t) <= window]
    if len(vals) < 2:
        return 0.0
    return float(np.std(vals) + 0.3 * np.mean(np.abs(np.diff(vals))))


def analyze(path: str, start: float, duration: float, cuts: list[float], fps: float = 5.0) -> dict:
    det = detector()
    samples: list[tuple[float, list[Face]]] = []
    for t, frame in sample_frames(path, start, duration, fps):
        try:
            samples.append((t, det(frame)))
        except Exception as exc:  # noqa: BLE001 - one bad frame shouldn't kill the render
            log.debug("face detection failed at %.2f: %s", t, exc)
            samples.append((t, []))
    tracks = build_tracks(samples, [start + c for c in cuts])
    return {"samples": samples, "tracks": tracks, "fps": fps}
