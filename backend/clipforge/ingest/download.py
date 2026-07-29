"""Fetch a video's media into the local library."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import httpx

from .. import logs
from ..config import paths
from ..models import Source, Video
from .listing import AUDIO_EXTS, VIDEO_EXTS, ytdlp_opts

log = logs.get("download")

Progress = Callable[[float, str], None]


def _dest_dir(video: Video) -> Path:
    d = paths().library / str(video.source_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_ytdlp_url(url: str) -> bool:
    return url.startswith("http") and not Path(url.split("?")[0]).suffix.lower() in VIDEO_EXTS | AUDIO_EXTS


def download(video: Video, src: Source, progress: Progress) -> Path:
    url = video.url
    dest = _dest_dir(video)
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in video.external_id)[-80:]

    if src.kind == "local_folder" or (url and Path(url).expanduser().exists()):
        p = Path(url).expanduser()
        target = dest / f"{safe_id}{p.suffix.lower()}"
        if not target.exists():
            progress(0.1, "copying local file")
            try:
                target.hardlink_to(p)
            except OSError:
                shutil.copy2(p, target)
        return target

    if src.kind == "drive":
        try:
            import gdown
        except ImportError as exc:
            raise RuntimeError("Google Drive sources need: pip install gdown") from exc
        progress(0.05, "downloading from Google Drive")
        out = gdown.download(url, str(dest / f"{safe_id}.mp4"), quiet=True, fuzzy=True)
        if not out:
            raise RuntimeError("Drive download failed; make sure the file is shared as 'anyone with the link'")
        return Path(out)

    if _is_ytdlp_url(url):
        for attempt in range(2):
            path, info = _ytdlp_fetch(url, dest, safe_id, progress, fresh=attempt > 0)
            if _has_video(path) or (info.get("vcodec") in (None, "none") and info.get("acodec") not in (None, "none")):
                break
            log.warning("download of %s has no video track; retrying from scratch", url)
        else:
            raise RuntimeError("downloaded file has no video track")
        video.title = video.title or info.get("title", "")
        video.description = (info.get("description") or "")[:5000]
        video.channel = video.channel or info.get("channel") or ""
        video.view_count = int(info.get("view_count") or video.view_count or 0)
        return path
    return _http_fetch(url, dest, safe_id, progress)


def _has_video(path: Path) -> bool:
    from ..media import probe

    try:
        pr = probe(path)
        return pr.width > 0 and pr.vcodec not in ("mjpeg", "png", "")
    except Exception:  # noqa: BLE001
        return False


def _ytdlp_fetch(url: str, dest: Path, safe_id: str, progress: Progress, fresh: bool) -> tuple[Path, dict]:
    import yt_dlp

    # Partial fragments or a half-merged file from an interrupted run poison resumes; start clean.
    for f in dest.glob(f"{safe_id}*"):
        if fresh or ".part" in f.name or (f.suffix in (".mp4", ".mkv", ".webm") and not _has_video(f)):
            f.unlink(missing_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progress(min(0.99, d.get("downloaded_bytes", 0) / total), f"downloading {d.get('_percent_str', '').strip()}")

    opts = ytdlp_opts(
        format="bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        merge_output_format="mp4",
        outtmpl=str(dest / f"{safe_id}.%(ext)s"),
        progress_hooks=[hook],
        ignoreerrors=False,
        overwrites=fresh,
        retries=10,
        fragment_retries=5,
        concurrent_fragment_downloads=4,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if path.suffix != ".mp4" and path.with_suffix(".mp4").exists():
        path = path.with_suffix(".mp4")
    if not path.exists():
        matches = sorted(p for p in dest.glob(f"{safe_id}.*") if ".part" not in p.name)
        if not matches:
            raise RuntimeError("download finished but the file is missing")
        path = matches[0]
    return path, info


def _http_fetch(url: str, dest: Path, safe_id: str, progress: Progress) -> Path:
    # Plain HTTP file (podcast enclosure, campaign asset).
    suffix = Path(url.split("?")[0]).suffix.lower() or ".mp4"
    target = dest / f"{safe_id}{suffix}"
    tmp = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    progress(done / total, f"downloading {done >> 20} / {total >> 20} MB")
    tmp.rename(target)
    return target
