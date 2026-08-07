"""Candidate segments: 20–90 s windows that start and end on clean sentence boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..understand.signals import sentence_intensity

HOOK_OPENERS = ("the ", "here's", "nobody", "everyone", "most people", "if you", "the biggest", "the worst",
                "the best", "the secret", "the truth", "never", "stop", "why ", "how ", "what ", "i was", "i had",
                "this is", "you need", "you have to", "my ", "i'll tell you", "let me tell you")


@dataclass
class Window:
    s0: int                 # first sentence index
    s1: int                 # last sentence index (inclusive)
    start: float
    end: float
    heuristic: float = 0.0
    features: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


def hook_signal(sent: dict) -> float:
    t = sent["text"].lower().lstrip("\"'")
    s = 0.0
    if any(t.startswith(h) for h in HOOK_OPENERS):
        s += 0.6
    if sent.get("question"):
        s += 0.5
    s += 0.35 * min(3, sent.get("triggers", 0))
    s += 0.25 * min(2, sent.get("numbers", 0))
    if 4 <= len(t.split()) <= 18:
        s += 0.3
    if sent.get("weak_open"):
        s -= 0.9
    s += 0.2 * max(0, sent.get("energy", 0))
    return s


def score_window(sents: list[dict], s0: int, s1: int) -> tuple[float, dict]:
    chunk = sents[s0:s1 + 1]
    intens = [sentence_intensity(s) for s in chunk]
    first = chunk[0]
    dur = chunk[-1]["e"] - first["s"]
    peak = max(intens)
    mean = sum(intens) / len(intens)
    laughs = sum(s.get("laughs", 0) for s in chunk)
    speakers = {s.get("speaker") for s in chunk if s.get("speaker")}
    rate = sum(s.get("rate", 0) for s in chunk) / len(chunk)
    ends_clean = chunk[-1]["text"].rstrip().endswith((".", "!", "?", "।"))
    dead_air = sum(min(2.0, s.get("gap_after", 0)) for s in chunk[:-1])
    hook = hook_signal(first)
    payoff = sentence_intensity(chunk[-1]) + 0.3 * chunk[-1].get("exclaim", False) + 0.4 * (laughs > 0)
    conf = sum(s.get("conf", 1) for s in chunk) / len(chunk)

    score = (2.2 * hook + 2.0 * peak + 1.4 * mean + 0.5 * min(3, laughs) + 0.8 * payoff
             + 0.6 * ends_clean - 0.35 * dead_air - (1.5 if conf < 0.55 else 0))
    # Sweet spot for podcast Shorts/Reels is ~25-50 s; longer clips must earn their length.
    if dur < 22:
        score -= 0.6
    elif dur > 50:
        score -= 0.6 * (dur - 50) / 20
    feats = {"hook_signal": round(hook, 3), "peak": round(peak, 3), "mean_intensity": round(mean, 3),
             "laughs": laughs, "speakers": len(speakers), "rate": round(rate, 2), "ends_clean": ends_clean,
             "dead_air": round(dead_air, 2), "duration": round(dur, 1), "conf": round(conf, 3)}
    return score, feats


def generate(timeline: dict, min_s: float = 20, max_s: float = 90) -> list[Window]:
    sents = timeline["sentences"]
    out: list[Window] = []
    for i in range(len(sents)):
        if sents[i].get("weak_open") and not sents[i].get("question"):
            continue
        start = sents[i]["s"]
        for j in range(i, len(sents)):
            dur = sents[j]["e"] - start
            if dur > max_s:
                break
            if dur < min_s:
                continue
            if not sents[j]["text"].rstrip().endswith((".", "!", "?", "।", "…")):
                continue
            score, feats = score_window(sents, i, j)
            out.append(Window(i, j, start, sents[j]["e"], score, feats))
    return out


def overlap(a: Window, b: Window) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def shortlist(windows: list[Window], k: int, max_overlap: float = 0.35) -> list[Window]:
    """Greedy non-maximum suppression: best windows first, skipping heavy overlaps."""
    chosen: list[Window] = []
    for w in sorted(windows, key=lambda w: w.heuristic, reverse=True):
        if all(overlap(w, c) <= max_overlap for c in chosen):
            chosen.append(w)
        if len(chosen) >= k:
            break
    return chosen
