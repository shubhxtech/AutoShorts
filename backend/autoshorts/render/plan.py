"""The edit director.

Editing rules (from analyses of high-performing podcast clips):
  - Change the framing every ~3–5 s so a talking head never goes static, but only at
    natural boundaries (jump cuts, sentence starts, phrase breaks), as hard cuts
    between wide / medium / close framings, like a multicam edit.
  - Punch in (animated) only on what the speaker actually stresses: words the LLM marks
    as carrying the point AND that are acoustically emphasised. One or two per clip.
  - At most one key-point overlay card; captions carry the rest.
  - Sound effects only where something visual happens (a transition); no music unless
    licensed tracks are supplied. Music sits ~20 dB under the voice.

Three presets: clean (default), hype (denser effects), minimal (captions + framing only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from .. import llm, logs
from .edl import EDL, OutWord

log = logs.get("plan")

MusicMood = Literal["none", "chill", "hype", "emotional", "suspense", "inspiring", "funny"]

PRESETS = {
    "clean":   {"punches": 2, "punch_gap": 7.0, "cards": 1, "emoji": False, "sfx": {"whoosh"}, "headline": True,
                "framing_every": (3.0, 5.0), "push_in": 0.025, "panel": True},
    "hype":    {"punches": 5, "punch_gap": 4.0, "cards": 2, "emoji": True, "sfx": {"whoosh", "pop", "hit"},
                "headline": True, "framing_every": (2.5, 4.0), "push_in": 0.035},
    # Talking head + motion-graphic inserts in the top half (explainer / "AI tools" reel style).
    "explainer": {"punches": 2, "punch_gap": 6.0, "cards": 4, "emoji": False, "sfx": {"whoosh", "pop"},
                  "headline": False, "framing_every": (3.0, 5.0), "push_in": 0.02, "panel": True},
    "minimal": {"punches": 0, "punch_gap": 99.0, "cards": 0, "emoji": False, "sfx": set(), "headline": False,
                "framing_every": (3.5, 6.0), "push_in": 0.02},
}
FRAMINGS = [1.0, 1.14, 1.0, 1.26]     # wide, medium, wide, close (relative to the auto close-up)


class Card(BaseModel):
    phrase: str = Field(description="exact 1-4 consecutive words from the transcript where the card appears")
    kind: Literal["stat", "title", "list", "question"] = Field(
        default="title", description="stat = big number + label; title = bold headline; list = 2-4 short items; question = thought bubble")
    text: str = Field(description="main text, max 5 words (for stat: the number, e.g. '#8' or '$12k')")
    label: str = Field(default="", description="for stat: what the number means, max 5 words")
    items: list[str] = Field(default_factory=list, description="for list: 2-4 items, max 4 words each")
    emoji: str = Field(default="", description="optional single emoji")


class Broll(BaseModel):
    phrase: str = Field(description="exact 1-4 consecutive transcript words where the cutaway starts")
    visual: str = Field(description="what the cutaway shows: an object, place or symbolic scene for what's said. NEVER people or faces (e.g. 'a cricket ball frozen mid-air over a stadium at dusk', not 'a man playing cricket')")


class EditPlanModel(BaseModel):
    headline: str = Field(description="persistent header for the whole clip, max 6 words, states the topic")
    punch_phrases: list[str] = Field(description="1-3 exact transcript words/short phrases the speaker stresses most; the edit punches in on these")
    emphasis: list[str] = Field(default_factory=list, description="3-8 exact transcript words that carry the meaning (highlighted in captions)")
    key_card: Card | None = Field(default=None, description="the single most useful on-screen card, or null if none would add information")
    extra_cards: list[Card] = Field(default_factory=list, description="0-3 more cards for other numbers, named tools/things, or lists the speaker walks through")
    broll: list[Broll] = Field(default_factory=list, description="0-2 cutaways for concrete things the speaker describes (a place, an object, an event). Skip abstract ideas and anything that would need a person.")
    music_mood: MusicMood = "none"
    cta: str = Field(description="end-screen call to action, max 6 words")
    loop: bool = Field(default=False, description="true if the last line naturally flows back into the first line")


PLAN_SYSTEM = """You are the editor of a high-retention short-form channel cutting podcast clips.
Every edit must be motivated by what is said: punch in only on the words the speaker leans on to make the point, add a card only if it adds information the viewer would otherwise miss (a number, a name, the key idea). If nothing deserves a card, return null.
Only use words that appear in the transcript for phrases."""


def plan_prompt(words: list[OutWord], hook: str, niche: str, language: str) -> str:
    lines, cur, start = [], [], 0.0
    for w in words:
        if not cur:
            start = w.start
        cur.append(w.text)
        if re.search(r"[.!?…।]$", w.text) or len(cur) >= 18:
            lines.append(f"({start:4.1f}s) {' '.join(cur)}")
            cur = []
    if cur:
        lines.append(f"({start:4.1f}s) {' '.join(cur)}")
    return (f"Niche: {niche or 'general'}\nLanguage: {language or 'en'} (headline, card text and CTA in this language; "
            f"for Hindi use Hinglish in Latin script)\nOn-screen hook for the first seconds: {hook}\n\n"
            "Transcript with output timestamps:\n" + "\n".join(lines) + "\n\nPlan the edit.")


# ---------------------------------------------------------------- timed events

@dataclass
class Punch:
    start: float
    end: float
    level: float


@dataclass
class CardEvent:
    start: float
    end: float
    text: str
    emoji: str = ""
    kind: str = "title"
    label: str = ""
    items: list = field(default_factory=list)
    panel: bool = False        # explainer layout: graphic in the top half, speaker below


@dataclass
class BrollEvent:
    start: float
    end: float
    visual: str
    image: str = ""


@dataclass
class EmojiPop:
    start: float
    emoji: str


@dataclass
class Sfx:
    at: float
    kind: str               # whoosh | pop | hit | ding
    gain_db: float = -18


@dataclass
class Timeline:
    duration: float
    preset: str = "clean"
    headline: str = ""
    cta: str = ""
    loop: bool = False
    music_mood: str = "none"
    framings: list[tuple[float, float]] = field(default_factory=list)   # (start, level) hard framing changes
    punches: list[Punch] = field(default_factory=list)
    cards: list[CardEvent] = field(default_factory=list)
    emoji: list[EmojiPop] = field(default_factory=list)
    broll: list[BrollEvent] = field(default_factory=list)
    sfx: list[Sfx] = field(default_factory=list)
    flashes: list[float] = field(default_factory=list)
    emphasis: set[int] = field(default_factory=set)     # OutWord indices highlighted in captions
    push_in: float = 0.025
    cta_keyword: str = ""      # "Comment KEYWORD" funnel card at the end

    # Kept for the stats panel.
    @property
    def zooms(self) -> list[Punch]:
        return self.punches

    def zoom_at(self, t: float) -> float:
        # 1. Framing level (hard cuts), with a slow push-in across each hold for subtle motion.
        level, since, until = 1.0, 0.0, self.duration
        for i, (st, lv) in enumerate(self.framings):
            if st <= t:
                level, since = lv, st
                until = self.framings[i + 1][0] if i + 1 < len(self.framings) else self.duration
        span = max(0.5, until - since)
        z = level * (1.0 + self.push_in * min(1.0, (t - since) / span))
        # 2. Punch-ins on stressed words: quick ease-in, hold, ease-out.
        for p in self.punches:
            if p.start - 0.1 <= t <= p.end + 0.25:
                attack = min(1.0, max(0.0, (t - (p.start - 0.1)) / 0.14))
                release = min(1.0, max(0.0, (p.end + 0.25 - t) / 0.25))
                k = _ease(min(attack, release))
                z *= 1.0 + (p.level - 1.0) * k
        return z


def _ease(x: float) -> float:
    return 1 - (1 - x) ** 3


def _norm(s: str) -> str:
    return re.sub(r"[^\w\u0900-\u097F']+", "", s.lower())


def find_phrase(words: list[OutWord], phrase: str, after: float = -1) -> int | None:
    toks = [_norm(t) for t in phrase.split() if _norm(t)]
    if not toks:
        return None
    norms = [_norm(w.text) for w in words]
    for i in range(len(words) - len(toks) + 1):
        if words[i].start < after:
            continue
        if norms[i:i + len(toks)] == toks:
            return i
    for i, n in enumerate(norms):
        if n == toks[0] and words[i].start >= after:
            return i
    return None


def ask_llm(words: list[OutWord], hook: str, niche: str, language: str) -> EditPlanModel | None:
    if not llm.available() or len(words) < 5:
        return None
    try:
        return llm.structured(EditPlanModel, PLAN_SYSTEM, plan_prompt(words, hook, niche, language),
                              max_tokens=900, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        log.warning("edit plan from LLM failed, using rules only: %s", exc)
        return None


def _boundaries(edl: EDL, words: list[OutWord]) -> list[tuple[float, float]]:
    """Candidate cut points with a priority: jump cuts > sentence starts > phrase breaks."""
    out: list[tuple[float, float]] = [(t, 3.0) for t in edl.boundaries()]
    for i in range(1, len(words)):
        prev, w = words[i - 1], words[i]
        gap = w.start - prev.end
        if re.search(r"[.!?…।]$", prev.text):
            out.append((max(prev.end, w.start - 0.04), 2.0))
        elif prev.text.endswith((",", ";", ":")) or gap > 0.18:
            out.append((max(prev.end, w.start - 0.04), 1.0))
        else:
            out.append((max(prev.end, w.start - 0.02), 0.2))   # any word gap, last resort for non-stop talkers
    return sorted(out)


def schedule(edl: EDL, model: EditPlanModel | None, *, emphasis_words: list[str], emoji_map: dict[str, str],
             hook_seconds: float, stress: dict[int, float] | None = None, preset: str = "clean",
             punch_zooms: bool = True, sfx: bool = True, end_card_s: float = 1.8, headline_fallback: str = "",
             cta_fallback: str = "Follow for more") -> Timeline:
    cfg = PRESETS.get(preset, PRESETS["clean"])
    words = edl.words
    dur = edl.duration
    stress = stress or {}
    tl = Timeline(duration=dur, preset=preset,
                  headline=((model.headline if model else headline_fallback).strip()[:60] if cfg["headline"] else ""),
                  cta=(model.cta if model else cta_fallback).strip()[:50], loop=bool(model and model.loop),
                  music_mood=model.music_mood if model else "none", push_in=cfg["push_in"])

    def w_stress(i: int) -> float:
        return stress.get(words[i].index, 0.0)

    # Caption emphasis: words that carry meaning, preferring the ones actually stressed aloud.
    wanted = {_norm(e) for e in (model.emphasis if model and model.emphasis else emphasis_words)}
    emph = {i for i, w in enumerate(words) if _norm(w.text) in wanted}
    stressed = sorted(range(len(words)), key=w_stress, reverse=True)
    for i in stressed[: max(2, int(dur / 4))]:
        if w_stress(i) >= 0.9:
            emph.add(i)
    # No more than one highlight every ~1.5 s, keeping the most stressed.
    kept: list[int] = []
    for i in sorted(emph, key=lambda i: (-(w_stress(i) + (1 if _norm(words[i].text) in wanted else 0)))):
        if all(abs(words[i].start - words[j].start) >= 1.5 for j in kept):
            kept.append(i)
    tl.emphasis = set(kept)

    # Transition between a cold open / narrated intro and the clip.
    for k, seg in enumerate(edl.segments[1:], start=1):
        if edl.segments[k - 1].kind in ("cold_open", "still"):
            tl.flashes.append(seg.out_start)
            if sfx and "whoosh" in cfg["sfx"]:
                tl.sfx.append(Sfx(max(0, seg.out_start - 0.3), "whoosh", -19))

    # Cards: the key one first, never during the hook or the end card.
    cards = []
    if model and model.key_card:
        cards.append(model.key_card)
    if model:
        cards += model.extra_cards
    used_until = hook_seconds + 0.6
    panel = bool(cfg.get("panel"))
    for card in cards[: cfg["cards"]]:
        i = find_phrase(words, card.phrase, after=used_until)
        if i is None or words[i].start > dur - end_card_s - 1.6:
            continue
        start = words[i].start
        hold = 2.6 if panel else 1.5
        if card.kind == "list":
            hold += 0.5 * max(0, len(card.items) - 2)
        tl.cards.append(CardEvent(start, min(start + hold, dur - end_card_s), card.text.strip()[:40], card.emoji[:4],
                                  kind=card.kind, label=card.label.strip()[:40],
                                  items=[x.strip()[:32] for x in card.items[:4]], panel=panel))
        if sfx and "pop" in cfg["sfx"] and panel:
            tl.sfx.append(Sfx(start, "pop", -21))
        if sfx and "hit" in cfg["sfx"]:
            tl.sfx.append(Sfx(start, "hit", -20))
        used_until = start + (hold + 1.8 if panel else 4.0)

    # AI B-roll cutaways (images are drawn later by the compositor).
    last_b = -10.0
    for br in (model.broll if model else [])[:2]:
        i = find_phrase(words, br.phrase, after=hook_seconds + 0.5)
        if i is None:
            continue
        t = words[i].start
        if t - last_b < 6 or t > dur - end_card_s - 2 or any(c.start - 1 <= t <= c.end + 1 for c in tl.cards):
            continue
        tl.broll.append(BrollEvent(t, min(t + 1.7, dur - end_card_s - 0.2), br.visual.strip()[:200]))
        last_b = t

    # Punch-ins: LLM-chosen phrases that are also stressed, else the most stressed key words.
    if punch_zooms and cfg["punches"]:
        cands: list[tuple[float, int]] = []
        for p in (model.punch_phrases if model else []):
            i = find_phrase(words, p)
            if i is not None:
                cands.append((2.0 + w_stress(i), i))
        for i in tl.emphasis:
            cands.append((w_stress(i), i))
        chosen: list[int] = []
        for score, i in sorted(cands, reverse=True):
            t = words[i].start
            if score < 0.6 or t < hook_seconds * 0.6 or t > dur - end_card_s:
                continue
            if any(abs(t - words[j].start) < cfg["punch_gap"] for j in chosen):
                continue
            if any(c.start - 0.6 <= t <= c.end + 0.4 for c in tl.cards) or any(b.start - 0.5 <= t <= b.end for b in tl.broll):
                continue
            chosen.append(i)
            if len(chosen) >= cfg["punches"]:
                break
        for i in sorted(chosen):
            end_i = min(len(words) - 1, i + 2)
            tl.punches.append(Punch(words[i].start, words[end_i].end + 0.15, 1.12))

    # Framing changes at natural boundaries every few seconds.
    lo, hi = cfg["framing_every"]
    bounds = _boundaries(edl, words)
    last, k = 0.0, 0
    tl.framings = [(0.0, FRAMINGS[0])]
    blocked = ([(p.start - 0.6, p.end + 0.3) for p in tl.punches] + [(c.start - 0.3, c.end + 0.3) for c in tl.cards]
               + [(b.start - 0.3, b.end + 0.3) for b in tl.broll])
    while True:
        window = [(t, pr) for t, pr in bounds if last + lo <= t <= last + hi and not any(a <= t <= b for a, b in blocked)]
        if not window:
            later = [(t, pr) for t, pr in bounds if t > last + hi and not any(a <= t <= b for a, b in blocked)]
            if not later:
                break
            window = later[:1]
        t, _ = max(window, key=lambda x: (x[1], -abs(x[0] - (last + (lo + hi) / 2))))
        if t > dur - 1.0:
            break
        k += 1
        tl.framings.append((t, FRAMINGS[k % len(FRAMINGS)]))
        last = t

    # Emoji pops (hype preset only).
    if cfg["emoji"]:
        emo = {_norm(k2): v for k2, v in emoji_map.items()}
        last_pop = -10.0
        for w in words:
            e = emo.get(_norm(w.text))
            if e and w.start - last_pop > 2.5 and hook_seconds < w.start < dur - end_card_s:
                tl.emoji.append(EmojiPop(w.start, e))
                if sfx and "pop" in cfg["sfx"]:
                    tl.sfx.append(Sfx(w.start, "pop", -22))
                last_pop = w.start
    tl.sfx.sort(key=lambda s: s.at)
    return tl
