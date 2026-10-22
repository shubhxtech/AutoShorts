"""Moment mining: shortlist windows, score them with the local LLM, keep the best, write packaging."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from sqlmodel import select

from .. import llm, logs, settings
from ..db import session
from ..models import Moment, Source, Video
from ..understand.signals import episode_keywords, norm_word
from . import candidates as cand
from .prompts import (CREATIVE_SYSTEM, HOOK_STYLES, REFINE_SYSTEM, SCORE_SYSTEM, SCOUT_SYSTEM, Creative, Hook,
                      MomentScore, Refined, ScoutResult, creative_prompt, refine_prompt, score_prompt, scout_prompt)

log = logs.get("miner")

DEFAULT_WEIGHTS = {"hook": 0.24, "standalone": 0.16, "emotion": 0.12, "surprise": 0.12, "value": 0.12,
                   "payoff": 0.12, "quotable": 0.12}


def score_weights() -> dict[str, float]:
    learned = settings.get("score_weights") or {}
    w = {k: float(learned.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}
    total = sum(w.values()) or 1
    return {k: v / total for k, v in w.items()}


def _lines(sents: list[dict], s0: int, s1: int, origin: float) -> list[tuple[int, float, str]]:
    return [(s["i"], s["s"] - origin, s["text"]) for s in sents[s0:s1 + 1]]


def _heuristic_scores(w: cand.Window) -> MomentScore:
    f = w.features
    def c(x):
        return max(0.0, min(10.0, x))
    return MomentScore(
        hook=c(3 + 2.2 * f["hook_signal"]), standalone=c(6 - 2 * (f["hook_signal"] < 0)),
        emotion=c(3 + 6 * f["peak"]), surprise=c(3 + 4 * f["mean_intensity"]), value=5,
        payoff=c(4 + 2 * f["ends_clean"] + f["laughs"]), quotable=c(3 + 5 * f["peak"]),
        best_start=w.s0, best_end=w.s1, hook_style="bold_claim", reason="heuristic score (local LLM unavailable)")


def _template_creative(sents: list[dict], w: cand.Window, keywords: list[str]) -> Creative:
    first = sents[w.s0]["text"]
    words = first.split()
    short = " ".join(words[:8]).rstrip(",.;:") + ("…" if len(words) > 8 else "")
    top = [k for k in keywords[:6]]
    hooks = [Hook(text=short, style="bold_claim"), Hook(text="Nobody talks about this…", style="curiosity_gap"),
             Hook(text="Wait for the end 👀", style="curiosity_gap"), Hook(text=f"The truth about {top[0]}" if top else "Listen to this", style="controversy")]
    strongest = max(range(w.s0, w.s1 + 1), key=lambda i: sents[i].get("peak", 0))
    return Creative(hooks=hooks, title=short, youtube_title=short[:80], youtube_description=first[:200], tags=top,
                    instagram_caption=f"{short}\n\nWhat do you think? 👇", hashtags=[t.replace(" ", "") for t in top] + ["shorts", "podcast"],
                    emphasis=top, emoji={}, punchline_line=strongest)


def _llm_score(sents, w: cand.Window, title: str, niche: str) -> MomentScore | None:
    try:
        res = llm.structured(MomentScore, SCORE_SYSTEM, score_prompt(_lines(sents, w.s0, w.s1, w.start), title, niche),
                             max_tokens=700, temperature=0.2, model=settings.get("llm_fast_model"))
    except llm.LLMUnavailable:
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM scoring failed for window %s-%s: %s", w.s0, w.s1, exc)
        return None
    # Keep LLM-proposed bounds only if they're inside the window and still a valid length.
    lo, hi = sorted((res.best_start, res.best_end))
    lo, hi = max(w.s0, lo), min(w.s1, hi)
    if hi <= lo or sents[hi]["e"] - sents[lo]["s"] < float(settings.get("min_moment_s")) * 0.9:
        lo, hi = w.s0, w.s1
    res.best_start, res.best_end = lo, hi
    return res


def _llm_scout(sents: list[dict], title: str, niche: str, min_s: float, max_s: float,
               progress: Callable[[float, str], None], window_words: int = 2200) -> list[cand.Window]:
    """Let the LLM skim the whole episode in overlapping sections and propose story arcs."""
    sections: list[tuple[int, int]] = []
    i = 0
    while i < len(sents):
        n, j = 0, i
        while j < len(sents) and n < window_words:
            n += len(sents[j]["text"].split())
            j += 1
        sections.append((i, j - 1))
        if j >= len(sents):
            break
        i = max(i + 1, j - max(3, (j - i) // 6))       # ~15% overlap so arcs at boundaries aren't lost
    out: list[cand.Window] = []
    for k, (a, b) in enumerate(sections):
        progress(0.3 * k / max(1, len(sections)), f"skimming the episode ({k + 1}/{len(sections)})")
        lines = [(x["i"], x["s"], x["text"]) for x in sents[a:b + 1]]
        try:
            res = llm.structured(ScoutResult, SCOUT_SYSTEM, scout_prompt(lines, title, niche), max_tokens=900,
                                 temperature=0.3, model=settings.get("llm_fast_model"))
        except Exception as exc:  # noqa: BLE001
            log.warning("scout pass failed on section %d: %s", k, exc)
            continue
        for p in res.picks:
            lo, hi = sorted((p.start_line, p.end_line))
            if not (a <= lo <= b and a <= hi <= b + 8 and hi < len(sents)):
                continue
            # Stretch or trim to a valid length on sentence boundaries.
            while hi + 1 < len(sents) and sents[hi]["e"] - sents[lo]["s"] < min_s:
                hi += 1
            while hi > lo and sents[hi]["e"] - sents[lo]["s"] > max_s * 1.6:
                hi -= 1
            dur = sents[hi]["e"] - sents[lo]["s"]
            if dur < min_s * 0.9:
                continue
            score, feats = cand.score_window(sents, lo, hi)
            feats["scout_reason"] = p.why[:120]
            out.append(cand.Window(lo, hi, sents[lo]["s"], sents[hi]["e"], score + 3.0, feats))
    log.info("scout proposed %d arcs", len(out))
    return out


TERMINAL = (".", "?", "!", "…", "।", '."', '?"', '!"')
# Hindi words a sentence can't start with (they attach to what came before).
HINDI_CONTINUATION = {"ने", "को", "से", "का", "की", "के", "में", "पर", "कि", "भी", "तक", "वाला", "वाली", "वाले"}


def snap_to_sentences(sents: list[dict], lo: int, hi: int) -> tuple[int, int]:
    """Whisper splits long sentences at pauses; never start or end a clip on a sentence fragment."""
    def continues(text: str) -> bool:
        first = text.strip()[:1]
        word = text.strip().split(" ")[0].strip(",") if text.strip() else ""
        # Lowercase start (Latin), a Hindi postposition/conjunction start, or any non-Latin line after a fragment.
        return first.islower() or word in HINDI_CONTINUATION

    while lo > 0 and not sents[lo - 1]["text"].rstrip().endswith(TERMINAL) and continues(sents[lo]["text"]) \
            and sents[hi]["e"] - sents[lo - 1]["s"] < 95:
        lo -= 1
    while lo > 0 and sents[lo]["text"].strip().split(" ")[0].strip(",") in HINDI_CONTINUATION \
            and sents[hi]["e"] - sents[lo - 1]["s"] < 95:
        lo -= 1
    while hi + 1 < len(sents) and not sents[hi]["text"].rstrip().endswith(TERMINAL) \
            and sents[hi + 1]["e"] - sents[lo]["s"] < 95:
        hi += 1
    return lo, hi


def _refine(sents: list[dict], lo: int, hi: int, title: str, min_s: float, max_s: float) -> dict:
    """The editor pass: widen/tighten boundaries with context, cut tangents, judge if it stands alone."""
    a, b = max(0, lo - 10), min(len(sents) - 1, hi + 15)
    lines = [(sents[i]["i"], sents[i]["s"] - sents[a]["s"], sents[i]["text"]) for i in range(a, b + 1)]
    try:
        r = llm.structured(Refined, REFINE_SYSTEM, refine_prompt(lines, lo, hi, title), max_tokens=700,
                           temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        log.warning("editor pass failed, keeping the scored bounds: %s", exc)
        return {"lo": lo, "hi": hi, "drop": [], "structure": "other", "note": "", "ok": True, "sense": True}
    nlo, nhi = sorted((r.start_line, r.end_line))
    nlo, nhi = max(a, nlo), min(b, nhi)
    nlo, nhi = snap_to_sentences(sents, nlo, nhi)
    drop = sorted({d for d in r.drop_lines if nlo < d < nhi})          # never drop the hook or the payoff
    dur = sum(sents[i]["e"] - sents[i]["s"] for i in range(nlo, nhi + 1) if i not in drop) + 0.3 * (nhi - nlo)
    # Too long even after drops: let the editor's call stand only if it's close; otherwise cut drops back.
    span = sents[nhi]["e"] - sents[nlo]["s"]
    ok = r.makes_sense_alone and r.complete and min_s * 0.8 <= dur <= max_s and span <= dur * 1.8 + 10
    why = "" if ok else f"sense={r.makes_sense_alone} complete={r.complete} dur={dur:.0f}s"
    return {"lo": nlo, "hi": nhi, "drop": drop, "structure": r.structure, "note": r.note, "ok": ok, "why": why,
            "sense": bool(r.makes_sense_alone), "hook": max(0.0, min(10.0, float(r.opening_hook or 5)))}


def weighted(scores: MomentScore, weights: dict[str, float]) -> float:
    return round(sum(getattr(scores, k) * v for k, v in weights.items()), 3)


def mine(video: Video, src: Source, progress: Callable[[float, str], None]) -> list[Moment]:
    timeline = json.loads(Path(video.timeline_path).read_text())
    transcript = json.loads(Path(video.transcript_path).read_text())
    sents = timeline["sentences"]
    if len(sents) < 3:
        return []
    min_s, max_s = float(settings.get("min_moment_s")), float(settings.get("max_moment_s"))
    windows = cand.generate(timeline, min_s, max_s)
    if not windows:
        return []
    k = int(settings.get("llm_candidates_per_video"))
    use_llm = llm.available()
    scouted = _llm_scout(sents, video.title, src.niche, min_s, max_s, progress) if use_llm else []
    # LLM-scouted arcs first (they read like an editor's picks), then the best heuristic windows.
    shortlist = cand.shortlist(scouted, k // 2) if scouted else []
    for w in cand.shortlist(windows, k):
        if len(shortlist) >= k:
            break
        if all(cand.overlap(w, c) <= 0.35 for c in shortlist):
            shortlist.append(w)
    heur = sorted(w.heuristic for w in shortlist)
    weights = score_weights()
    keywords = episode_keywords(transcript["words"], 30)

    scored: list[tuple[cand.Window, MomentScore, float]] = []
    for idx, w in enumerate(shortlist):
        progress(0.3 + 0.4 * idx / max(1, len(shortlist)), f"scoring moment {idx + 1}/{len(shortlist)}")
        res = _llm_score(sents, w, video.title, src.niche) if use_llm else None
        if res is None:
            res = _heuristic_scores(w)
        rank = heur.index(w.heuristic) / max(1, len(heur) - 1)       # 0..1 heuristic percentile
        final = 0.8 * weighted(res, weights) + 0.2 * (rank * 10) if use_llm else weighted(res, weights)
        scored.append((w, res, round(final, 3)))

    from ..analytics.predictor import predict_moment

    keep_n = int(settings.get("moments_per_video"))
    min_final = float(settings.get("min_final_score"))
    scored.sort(key=lambda t: t[2], reverse=True)

    # Editor pass: fix boundaries with surrounding context, cut tangents, reject clips that don't stand alone.
    chosen: list[tuple[cand.Window, MomentScore, float, dict]] = []
    near_miss: list[tuple[float, cand.Window, MomentScore, float, dict]] = []
    for w, res, final in scored:
        if len(chosen) >= keep_n:
            break
        if final < min_final and len(chosen) >= 2:
            continue
        if res.hook < 4 and len(near_miss) >= 4:
            continue
        edit_ = _refine(sents, res.best_start, res.best_end, video.title, min_s, max_s) if use_llm else \
            {"lo": res.best_start, "hi": res.best_end, "drop": [], "structure": "other", "note": "", "ok": True, "sense": True}
        progress(0.7 + 0.1 * len(chosen) / max(1, keep_n), f"editing clip {len(chosen) + 1}/{keep_n}")
        if "hook" in edit_:
            res.hook = edit_["hook"]         # the editor may have moved the start to a stronger line
        weak_hook = res.hook < 4            # a weak first line loses the viewer before the payoff
        if not edit_["ok"] or weak_hook:
            log.info("editor %s lines %s-%s: %s", "held back" if edit_.get("sense") else "rejected",
                     res.best_start, res.best_end, edit_.get("why") or "weak hook")
            if edit_.get("sense"):
                near_miss.append((final - (2 if weak_hook else 0), w, res, final, edit_))
            continue
        span = cand.Window(edit_["lo"], edit_["hi"], sents[edit_["lo"]]["s"], sents[edit_["hi"]]["e"])
        if any(cand.overlap(span, cand.Window(e["lo"], e["hi"], sents[e["lo"]]["s"], sents[e["hi"]]["e"])) > 0.3
               for _, _, _, e in chosen):
            continue
        res.best_start, res.best_end = edit_["lo"], edit_["hi"]
        chosen.append((w, res, final, edit_))

    # Never leave a video empty: if the strict pass found fewer than two clips, use the best near-misses
    # (clips the editor said make sense, but that were a little long, loose or slow to hook).
    if len(chosen) < 2 and near_miss:
        for _, w, res, final, edit_ in sorted(near_miss, key=lambda x: -x[0]):
            if len(chosen) >= max(2, min(keep_n, 3)):
                break
            span = cand.Window(edit_["lo"], edit_["hi"], sents[edit_["lo"]]["s"], sents[edit_["hi"]]["e"])
            if any(cand.overlap(span, cand.Window(e["lo"], e["hi"], sents[e["lo"]]["s"], sents[e["hi"]]["e"])) > 0.3
                   for _, _, _, e in chosen):
                continue
            # If too many lines were dropped, fall back to the continuous span.
            if sents[edit_["hi"]]["e"] - sents[edit_["lo"]]["s"] > max_s * 1.3:
                lo_, hi_ = edit_["lo"], edit_["hi"]
                while hi_ > lo_ and sents[hi_]["e"] - sents[lo_]["s"] > max_s:
                    hi_ -= 1
                if sents[hi_]["e"] - sents[lo_]["s"] < min_s:
                    continue                  # can't be made into a sensible clip; skip rather than ship a stub
                edit_ = {**edit_, "drop": [], "hi": hi_}
            res.best_start, res.best_end = edit_["lo"], edit_["hi"]
            chosen.append((w, res, final, edit_))

    moments: list[Moment] = []
    with session() as s:
        for old in s.exec(select(Moment).where(Moment.video_id == video.id, Moment.status == "candidate")).all():
            s.delete(old)
        s.commit()

    for idx, (w, res, final, edit_) in enumerate(chosen):
        progress(0.8 + 0.2 * idx / max(1, len(chosen)), f"writing hooks {idx + 1}/{len(chosen)}")
        lo, hi = res.best_start, res.best_end
        keep_lines = [i for i in range(lo, hi + 1) if i not in set(edit_["drop"])]
        creative = None
        if use_llm:
            try:
                creative = llm.structured(Creative, CREATIVE_SYSTEM,
                                          creative_prompt([(sents[i]["i"], sents[i]["s"] - sents[lo]["s"], sents[i]["text"]) for i in keep_lines], video.title,
                                                          src.niche, _creative_language(video, src)),
                                          max_tokens=1400, temperature=0.6)
            except Exception as exc:  # noqa: BLE001
                log.warning("LLM packaging failed: %s", exc)
        if creative is None:
            creative = _template_creative(sents, cand.Window(lo, hi, sents[lo]["s"], sents[hi]["e"]), keywords)
        creative = _clean_creative(creative, transcript["words"], sents, lo, hi)

        punch = creative.punchline_line if lo <= creative.punchline_line <= hi else None
        features = dict(w.features)
        features.update({k: getattr(res, k) for k in DEFAULT_WEIGHTS})
        drop_ranges = [[sents[i]["s"] - 0.02, sents[i]["e"] + 0.02] for i in edit_["drop"]]
        m = Moment(
            video_id=video.id, start_s=sents[lo]["s"], end_s=sents[hi]["e"],
            tight_start_s=None, tight_end_s=None,
            text=" ".join(sents[i]["text"] for i in keep_lines),
            title=creative.title, hooks=[h.model_dump() for h in creative.hooks],
            scores={k: getattr(res, k) for k in DEFAULT_WEIGHTS},
            heuristic_score=round(w.heuristic, 3), llm_score=weighted(res, weights), final_score=final,
            hook_style=res.hook_style, reason=(edit_.get("note") or res.reason),
            punchline_s=([sents[punch]["s"], sents[punch]["e"]] if punch is not None and punch != lo
                         and punch not in edit_["drop"] and edit_["structure"] != "question_answer" else None),
            emphasis=creative.emphasis, emoji=creative.emoji,
            meta={"youtube_title": creative.youtube_title, "youtube_description": creative.youtube_description,
                  "tags": creative.tags, "instagram_caption": creative.instagram_caption,
                  "hashtags": creative.hashtags, "drop": drop_ranges, "structure": edit_["structure"]},
            features=features, status="selected",
        )
        m.predicted_score = predict_moment(m)
        with session() as s:
            s.add(m)
            s.commit()
            s.refresh(m)
        moments.append(m)
    llm.unload()
    return moments


def _creative_language(video: Video, src: Source) -> str:
    from ..understand.transcribe import allowed_language

    lang = allowed_language((video.language or "en").lower())
    if lang == "hi":
        return "hinglish" if src.caption_script == "latin" else "hindi_devanagari"
    return lang


def _clean_creative(c: Creative, words: list[dict], sents: list[dict], lo: int, hi: int) -> Creative:
    clip_words = {norm_word(w["w"]) for w in words[sents[lo]["w0"]:sents[hi]["w1"]]}
    c.emphasis = [e for e in c.emphasis if norm_word(e) in clip_words][:10]
    c.emoji = {k: v for k, v in (c.emoji or {}).items() if norm_word(k) in clip_words and len(v) <= 4}
    seen, hooks = set(), []
    for h in c.hooks:
        t = re.sub(r"\s+", " ", h.text.replace("#", "")).strip().strip('"')
        if t and t.lower() not in seen:
            seen.add(t.lower())
            hooks.append(Hook(text=t[:70], style=h.style))
    if not hooks:
        hooks = [Hook(text=sents[lo]["text"][:60], style="bold_claim")]
    c.hooks = hooks[:5]
    c.hashtags = [re.sub(r"[^\w]", "", h.lstrip("#")) for h in c.hashtags if h.strip("# ")][:15]
    c.tags = [t.strip() for t in c.tags if t.strip()][:12]
    c.youtube_title = c.youtube_title.strip()[:95]
    return c


__all__ = ["mine", "HOOK_STYLES", "score_weights"]
