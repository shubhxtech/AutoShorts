"""Edit decision list: which source intervals end up in the clip, in what order.

Builds the cut from word timestamps so every edit lands in a gap between
words: dead air is tightened, filler words and stutters are removed, and an
optional cold open teases the punchline before the clip starts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LEADING_FILLER = {"and", "so", "but", "um", "uh", "like", "yeah", "okay", "ok", "well", "because", "cause", "anyway",
                  # Hindi / Hinglish openers
                  "तो", "और", "लेकिन", "पर", "मतलब", "यानी", "अच्छा", "हाँ", "हां", "ना", "कि", "वो", "toh", "aur", "matlab",
                  "yaani", "accha", "haan"}
FILLERS = {"um", "uh", "umm", "uhh", "erm", "er", "hmm", "mm", "mhm", "uhm", "ah", "eh"}


def _norm(w: str) -> str:
    return re.sub(r"[^\w\u0900-\u097F']+", "", w.lower())


@dataclass
class Segment:
    src_start: float
    src_end: float
    kind: str = "main"          # main | cold_open | still
    zoom: float = 1.0           # base framing for this segment (alternates across jump cuts)
    out_start: float = 0.0
    still_at: float | None = None   # for "still" segments: source frame to hold

    @property
    def duration(self) -> float:
        return self.src_end - self.src_start


@dataclass
class OutWord:
    index: int                  # index into the episode word list
    text: str
    start: float                # output timeline
    end: float
    src_start: float
    prob: float = 1.0


@dataclass
class EDL:
    segments: list[Segment] = field(default_factory=list)
    words: list[OutWord] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def finalize(self, all_words: list[dict]) -> EDL:
        t = 0.0
        self.words = []
        for seg in self.segments:
            seg.out_start = t
            if seg.kind != "still":
                for i, w in enumerate(all_words):
                    if w["s"] >= seg.src_start - 1e-3 and w["e"] <= seg.src_end + 1e-3 and _norm(w["w"]) not in FILLERS:
                        self.words.append(OutWord(i, w["w"], t + w["s"] - seg.src_start, t + w["e"] - seg.src_start,
                                                  w["s"], w.get("p", 1.0)))
            t += seg.duration
        return self

    def segment_at(self, t_out: float) -> Segment | None:
        for seg in self.segments:
            if seg.out_start <= t_out < seg.out_start + seg.duration:
                return seg
        return self.segments[-1] if self.segments else None

    def to_source(self, t_out: float) -> float:
        seg = self.segment_at(t_out)
        if seg is None:
            return 0.0
        if seg.kind == "still":
            return seg.still_at if seg.still_at is not None else seg.src_start
        return seg.src_start + (t_out - seg.out_start)

    def boundaries(self) -> list[float]:
        """Output times where one segment ends and another begins."""
        return [s.out_start for s in self.segments[1:]]

    def to_json(self) -> list:
        return [[round(s.src_start, 3), round(s.src_end, 3), s.kind, s.zoom] for s in self.segments]


def _words_in(words: list[dict], start: float, end: float) -> list[int]:
    return [i for i, w in enumerate(words) if w["s"] >= start - 0.05 and w["e"] <= end + 0.05]


def build(words: list[dict], start: float, end: float, *, jump_cuts: bool = True, max_pause: float = 0.45,
          keep_pause: float = 0.14, remove_fillers: bool = True, cold_open: tuple[float, float] | None = None,
          alt_zoom: float = 1.1, lead_in: float = 0.06, tail: float = 0.35,
          exclude: list | None = None) -> EDL:
    """`exclude`: source ranges to cut out of the middle (tangents, interruptions) chosen by the editor pass."""
    idx = _words_in(words, start, end)
    if not idx:
        return EDL([Segment(start, end)])
    # Editors trim a leading "And…", "So…", "But…": open on the real first word.
    dropped = 0
    while len(idx) > 6 and dropped < 2 and _norm(words[idx[0]]["w"]) in LEADING_FILLER:
        idx = idx[1:]
        dropped += 1
    first, last = words[idx[0]], words[idx[-1]]
    # Pad into the silence around the clip, but never into a neighbouring word.
    prev_end = words[idx[0] - 1]["e"] + 0.03 if idx[0] > 0 else 0.0
    next_start = words[idx[-1] + 1]["s"] - 0.04 if idx[-1] + 1 < len(words) else float("inf")
    # (Whisper's word times can overlap; never let the padding start inside the first word.)
    a = max(start - 0.3, first["s"] - lead_in, min(prev_end, first["s"] - 0.01))
    b = max(last["e"] + 0.02, min(last["e"] + tail, next_start))

    # Cut list: (cut_start, cut_end) in source time, always inside gaps between kept words.
    cuts: list[tuple[float, float]] = []
    def excluded(w: dict) -> bool:
        mid = (w["s"] + w["e"]) / 2
        return any(a <= mid <= b for a, b in (exclude or []))

    kept = [i for i in idx if not (remove_fillers and _norm(words[i]["w"]) in FILLERS) and not excluded(words[i])]
    if remove_fillers:
        # Stutters: "I I think" -> drop the first repeat.
        dedup = []
        for k, i in enumerate(kept):
            if k + 1 < len(kept) and _norm(words[i]["w"]) == _norm(words[kept[k + 1]]["w"]) \
                    and words[kept[k + 1]]["s"] - words[i]["e"] < 0.4 and len(_norm(words[i]["w"])) <= 4:
                continue
            dedup.append(i)
        kept = dedup
    for k in range(len(kept) - 1):
        w0, w1 = words[kept[k]], words[kept[k + 1]]
        gap = w1["s"] - w0["e"]
        removed_between = kept[k + 1] - kept[k] > 1
        if removed_between or (jump_cuts and gap > max_pause):
            pad = min(keep_pause, max(0.0, gap)) / 2
            if removed_between:
                # Pad against the removed words themselves, so no edge lands inside a filler.
                first, last = words[kept[k] + 1], words[kept[k + 1] - 1]
                c0 = w0["e"] + min(pad, max(0.0, first["s"] - w0["e"]) / 2)
                c1 = w1["s"] - min(pad, max(0.0, w1["s"] - last["e"]) / 2)
            else:
                c0, c1 = w0["e"] + pad, w1["s"] - pad
            if c1 - c0 > 0.08:
                cuts.append((c0, c1))

    segs: list[Segment] = []
    cursor = a
    for c0, c1 in cuts:
        if c0 - cursor >= 0.25:
            segs.append(Segment(cursor, c0))
            cursor = c1
        elif segs:
            # Too short to stand alone: extend the previous segment instead of making a flash cut.
            segs[-1].src_end = c0
            cursor = c1
        else:
            if c0 - cursor > 0.08:
                segs.append(Segment(cursor, c0))   # keep a short first word rather than drop it
            cursor = c1
    if b - cursor > 0.1:
        segs.append(Segment(cursor, b))

    # Alternate framing across jump cuts so they read as deliberate camera changes.
    level = 1.0
    for k, seg in enumerate(segs):
        if k > 0 and segs[k - 1].duration > 0.9:
            level = alt_zoom if level == 1.0 else 1.0
        seg.zoom = level

    if cold_open:
        ps, pe = cold_open
        tease = _words_in(words, ps, pe)
        while len(tease) > 4 and _norm(words[tease[0]]["w"]) in LEADING_FILLER:
            tease = tease[1:]
        if tease:
            before = words[tease[0] - 1]["e"] + 0.02 if tease[0] > 0 else 0.0
            t0 = max(before, words[tease[0]]["s"] - 0.04)
            # End the tease on a word boundary, at most ~3.2 s in.
            t1 = t0
            for i in tease:
                if words[i]["e"] - t0 > 3.2 and t1 > t0 + 1.2:
                    break
                after = words[i + 1]["s"] - 0.02 if i + 1 < len(words) else words[i]["e"] + 0.05
                t1 = min(words[i]["e"] + 0.05, max(words[i]["e"] + 0.01, after))
            if t1 - t0 >= 1.0:
                segs.insert(0, Segment(t0, t1, kind="cold_open", zoom=1.15))
    return EDL(segs).finalize(words)


def prepend_still(edl: EDL, duration: float, at: float, words: list[dict]) -> EDL:
    """Hold a frame for a narrated intro."""
    edl.segments.insert(0, Segment(at, at + duration, kind="still", zoom=1.08, still_at=at))
    return edl.finalize(words)
