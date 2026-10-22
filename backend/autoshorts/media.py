"""Thin wrappers around ffmpeg / ffprobe."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import logs

log = logs.get("media")


def _bin(name: str) -> str:
    for cand in (os.environ.get(f"AUTOSHORTS_{name.upper()}"), shutil.which(name),
                 f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError(f"{name} not found. Install it: macOS `brew install ffmpeg`, Windows `winget install ffmpeg`, "
                       "Linux `sudo apt install ffmpeg` (or set AUTOSHORTS_FFMPEG to its path)")


def ffmpeg_bin() -> str:
    return _bin("ffmpeg")


def ffprobe_bin() -> str:
    return _bin("ffprobe")


def run(args: list[str], check: bool = True, capture: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=capture, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(f"{Path(args[0]).name} failed ({proc.returncode}): {tail}")
    return proc


def ffmpeg(*args: str, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    return run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", *args], check=check, timeout=timeout)


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    vcodec: str
    acodec: str
    rotation: int = 0


def probe(path: str | Path) -> Probe:
    out = run([ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
               str(path)]).stdout
    data = json.loads(out)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or (v or {}).get("duration") or 0)
    fps = 0.0
    if v:
        for key in ("avg_frame_rate", "r_frame_rate"):
            num, _, den = (v.get(key) or "0/1").partition("/")
            try:
                f = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                f = 0
            if 1 < f < 240:
                fps = f
                break
    rotation = 0
    if v:
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = int(sd["rotation"])
        rotation = int((v.get("tags") or {}).get("rotate", rotation))
    w, h = (int(v.get("width", 0)), int(v.get("height", 0))) if v else (0, 0)
    if abs(rotation) in (90, 270):
        w, h = h, w
    return Probe(duration=duration, width=w, height=h, fps=fps or 30.0, has_audio=a is not None,
                 vcodec=(v or {}).get("codec_name", ""), acodec=(a or {}).get("codec_name", ""), rotation=rotation)


def extract_audio(src: str | Path, dst: str | Path, sr: int = 16000) -> Path:
    """Mono 16 kHz WAV for transcription and audio analysis."""
    ffmpeg("-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst))
    return Path(dst)


def load_wav_mono(path: str | Path) -> tuple[np.ndarray, int]:
    import wave

    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        width = w.getsampwidth()
        channels = w.getnchannels()
    if width != 2:
        raise ValueError("expected 16-bit PCM")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, sr


def file_hash(path: str | Path) -> str:
    """Fast content fingerprint: size plus the first and last 4 MB."""
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(p, "rb") as f:
        h.update(f.read(4 << 20))
        if size > 8 << 20:
            f.seek(-(4 << 20), os.SEEK_END)
            h.update(f.read(4 << 20))
    return h.hexdigest()


@lru_cache
def encoders() -> set[str]:
    out = run([ffmpeg_bin(), "-hide_banner", "-encoders"], check=False).stdout
    return {m.group(1) for m in re.finditer(r"^\s*V\S*\s+(\S+)", out, re.M)}


@lru_cache
def encoder_works(name: str) -> bool:
    """Builds list NVENC/QSV/AMF even without that GPU: try a 0.1 s encode before trusting one."""
    if name not in encoders():
        return False
    proc = run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                "-c:v", name, "-f", "null", "-"], check=False)
    return proc.returncode == 0


HW_ENCODERS = ["h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"]


def video_encoder_args(preference: str = "auto", bitrate: str = "10M") -> list[str]:
    encs = encoders()
    maxrate = bitrate.rstrip("M")
    try:
        maxrate = f"{int(float(maxrate) * 1.4)}M"
    except ValueError:
        maxrate = bitrate
    wanted = {"videotoolbox": "h264_videotoolbox", "nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf"}
    order = [wanted[preference]] if preference in wanted else HW_ENCODERS if preference == "auto" else []
    hw = next((e for e in order if encoder_works(e)), None)
    rate = ["-b:v", bitrate, "-maxrate", maxrate, "-bufsize", maxrate]
    if hw == "h264_videotoolbox":
        return ["-c:v", hw, "-profile:v", "high", *rate, "-allow_sw", "1"]
    if hw == "h264_nvenc":
        return ["-c:v", hw, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-profile:v", "high", *rate]
    if hw == "h264_qsv":
        return ["-c:v", hw, "-preset", "medium", "-profile:v", "high", *rate]
    if hw == "h264_amf":
        return ["-c:v", hw, "-quality", "quality", "-rc", "vbr_peak", "-profile:v", "high", *rate]
    if "libx264" in encs:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-profile:v", "high",
                "-maxrate", "14M", "-bufsize", "20M"]
    return ["-c:v", "h264"]


def loudness(path: str | Path) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP) via ebur128."""
    proc = ffmpeg("-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-", check=False)
    err = proc.stderr or ""
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else err
    i = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)
    def num(m):
        if not m:
            return None
        return float("-inf") if m.group(1) == "-inf" else float(m.group(1))
    return {"lufs": num(i), "true_peak": num(tp)}


def black_segments(path: str | Path, min_d: float = 0.25) -> list[tuple[float, float]]:
    proc = ffmpeg("-i", str(path), "-an", "-vf", f"blackdetect=d={min_d}:pix_th=0.08", "-f", "null", "-",
                  check=False)
    segs = []
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", proc.stderr or ""):
        segs.append((float(m.group(1)), float(m.group(2))))
    return segs


def scene_cuts(path: str | Path, start: float, duration: float, threshold: float = 0.32) -> list[float]:
    """Hard cuts (seconds, relative to `start`) using ffmpeg's scene score on a small proxy."""
    proc = ffmpeg("-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path), "-an",
                  "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-", check=False)
    cuts = []
    for m in re.finditer(r"pts_time:([\d.]+)", proc.stderr or ""):
        cuts.append(float(m.group(1)))
    return sorted(set(round(c, 3) for c in cuts))


def thumbnail(src: str | Path, at: float, dst: str | Path, width: int = 540) -> Path:
    ffmpeg("-ss", f"{max(0, at):.3f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
           "-q:v", "3", str(dst))
    return Path(dst)
