"""Automatic quality checks. A clip that fails any blocking check never reaches review."""

from __future__ import annotations

from pathlib import Path

from . import settings
from .media import black_segments, loudness, probe
from .models import Moment, Source


def _check(name: str, passed: bool, detail: str, blocking: bool = True) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "blocking": blocking}


def run(path: str | Path, stats: dict, moment: Moment, src: Source, words: list[dict]) -> tuple[bool, list[dict], dict]:
    results: list[dict] = []
    try:
        pr = probe(path)
    except Exception as exc:
        return False, [_check("file", False, f"unreadable: {exc}")], {}
    results.append(_check("format", pr.width == 1080 and pr.height == 1920 and pr.vcodec == "h264" and pr.has_audio,
                          f"{pr.width}x{pr.height} {pr.vcodec}, audio={'yes' if pr.has_audio else 'no'}"))

    d = pr.duration
    yt_max = float(settings.get("max_duration_youtube"))
    ig_max = float(settings.get("max_duration_instagram"))
    min_d = float(settings.get("min_duration"))
    fits = {"youtube": min_d <= d <= yt_max, "instagram": min_d <= d <= ig_max}
    allowed = [p for p in (src.allowed_platforms or []) if p in fits]
    ok_any = any(fits[p] for p in allowed) if allowed else False
    results.append(_check("duration", ok_any, f"{d:.1f}s — fits " + (", ".join(p for p in allowed if fits[p]) or "no allowed platform")))

    blacks = [(a, b) for a, b in black_segments(path) if b - a > 0.3]
    results.append(_check("no_black_frames", not blacks, "none" if not blacks else
                          ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in blacks[:3])))

    target = float(settings.get("target_lufs"))
    ln = loudness(path)
    lufs, tp = ln.get("lufs"), ln.get("true_peak")
    loud_ok = lufs is not None and abs(lufs - target) <= 1.5 and (tp is None or tp <= -0.5)
    results.append(_check("loudness", loud_ok, f"{lufs if lufs is not None else '?'} LUFS, peak {tp if tp is not None else '?'} dBFS"))

    fif = stats.get("face_in_frame")
    results.append(_check("face_in_frame", fif is None or fif >= 0.85,
                          "no face layout" if fif is None else f"{fif * 100:.0f}% of frames"))

    # Every cut must fall between words.
    bad_cuts = []
    for seg in stats.get("edl", []):
        for t in (seg[0], seg[1]):
            for w in words:
                if w["s"] + 0.03 < t < w["e"] - 0.03:
                    bad_cuts.append(t)
                    break
    results.append(_check("no_mid_word_cuts", not bad_cuts, "clean" if not bad_cuts else f"{len(bad_cuts)} cut(s) inside a word"))

    ow = stats.get("words", [])
    monotonic = all(b[2] >= a[2] for a, b in zip(ow, ow[1:]))
    in_range = all(0 <= w[2] <= d + 0.1 for w in ow)
    conf = sum(w[4] for w in ow) / len(ow) if ow else 0
    results.append(_check("captions_in_sync", bool(ow) and monotonic and in_range and conf >= 0.5,
                          f"{len(ow)} words, avg confidence {conf:.2f}"))

    first = stats.get("first_word_at")
    hook_score = float((moment.scores or {}).get("hook", 0))
    opening = (stats.get("spec") or {}).get("opening", "straight")
    # A cold open / narrated intro replaces the weak first line with the strongest one.
    strong_open = opening in ("cold_open", "narrated") and len(stats.get("edl") or []) > 1
    hook_ok = bool(stats.get("hook_until")) and first is not None and first <= 1.2 and (
        hook_score >= 5 or strong_open or not moment.scores)
    results.append(_check("hook_in_first_3s", hook_ok,
                          f"speech at {first if first is None else round(first, 2)}s, hook overlay "
                          f"{'on' if stats.get('hook_until') else 'off'}, hook score {hook_score:.0f}/10"))

    gaps = [b[2] - a[3] for a, b in zip(ow, ow[1:])]
    max_gap = max(gaps) if gaps else 0
    results.append(_check("no_dead_air", max_gap <= 1.5, f"longest silence {max_gap:.2f}s",
                          blocking=bool(settings.get("jump_cuts"))))

    passed = all(r["passed"] for r in results if r["blocking"])
    return passed, results, fits
