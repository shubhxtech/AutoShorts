"""Cheap audio and text signals that point at likely viral moments.

Output is the episode "timeline": per-sentence features plus an energy curve.
The moment miner uses these to shortlist candidates before the LLM reads them.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from ..media import load_wav_mono

HOP_S = 0.1

STOPWORDS = set("""
a an the and or but so if then than that this these those is are was were be been being am do does did done
have has had having i me my mine we us our you your yours he him his she her hers it its they them their
what which who whom whose where when why how all any both each few more most other some such no nor not only
own same too very can will just should now of in on at by for with about against between into through during
before after above below to from up down out off over under again further once here there also yeah yes okay ok
like um uh gonna wanna kind sort really actually basically literally right know mean thing things get got
going go one two lot little bit something anything everything stuff way
""".split())

# Words that tend to open or carry high-retention moments.
TRIGGERS = {
    "secret", "secrets", "mistake", "mistakes", "never", "nobody", "everyone", "truth", "lie", "lied", "crazy",
    "insane", "worst", "best", "biggest", "million", "billion", "money", "rich", "broke", "fired", "quit", "died",
    "death", "scared", "afraid", "shocking", "shocked", "honestly", "regret", "wrong", "stop", "warning", "hack",
    "trick", "rule", "rules", "why", "how", "reason", "nobody's", "changed", "failed", "failure", "success",
    "habit", "habits", "brain", "dangerous", "illegal", "banned", "free", "hate", "love", "fear", "cheat",
    "cheated", "divorce", "addicted", "addiction", "toxic", "real", "exactly", "problem", "important",
}
INTENSIFIERS = {"so", "very", "extremely", "absolutely", "totally", "completely", "literally", "insanely",
                "incredibly", "massive", "huge", "!"}
EMOTION = {
    "love", "hate", "angry", "furious", "cried", "crying", "tears", "happy", "sad", "depressed", "anxious",
    "anxiety", "terrified", "scared", "excited", "amazing", "terrible", "horrible", "beautiful", "painful",
    "hurt", "proud", "ashamed", "embarrassed", "jealous", "grateful", "shocked", "heartbroken", "lonely",
}
WEAK_OPENERS = {"and", "but", "so", "because", "which", "also", "then", "or", "like", "um", "uh", "yeah", "right",
                "he", "she", "they", "it", "that", "this", "those", "these", "him", "her", "them", "there"}
LAUGH_MARKERS = re.compile(r"\b(ha(ha)+|lol|laughs?|laughter|haha)\b|\[laugh|\(laugh", re.I)


def norm_word(w: str) -> str:
    return re.sub(r"[^\w\u0900-\u097F']+", "", w.lower())


def energy_curve(audio: np.ndarray, sr: int, hop_s: float = HOP_S) -> np.ndarray:
    hop = int(sr * hop_s)
    n = len(audio) // hop
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-10)
    return 20 * np.log10(rms + 1e-10)


def laughter_times(audio: np.ndarray, sr: int, words: list[dict], energy_db: np.ndarray) -> list[float]:
    """Laughs: transcript markers plus loud, noisy bursts between words."""
    times = [w["s"] for w in words if LAUGH_MARKERS.search(w["w"])]
    if not len(energy_db):
        return times
    speech = np.zeros(len(energy_db), dtype=bool)
    for w in words:
        a, b = int(w["s"] / HOP_S), int(w["e"] / HOP_S) + 1
        speech[a:b] = True
    loud = energy_db > (np.percentile(energy_db, 80))
    hop = int(sr * HOP_S)
    cand = np.where(loud & ~speech)[0]
    for idx in cand:
        seg = audio[idx * hop:(idx + 1) * hop]
        if len(seg) < hop:
            continue
        zcr = np.mean(np.abs(np.diff(np.sign(seg)))) / 2
        if zcr > 0.08:  # breathy, noisy burst rather than a tone
            times.append(idx * HOP_S)
    times.sort()
    merged: list[float] = []
    for t in times:
        if not merged or t - merged[-1] > 1.0:
            merged.append(round(t, 2))
    return merged


def episode_keywords(words: list[dict], top: int = 40) -> list[str]:
    counts = Counter(nw for w in words if (nw := norm_word(w["w"])) and nw not in STOPWORDS and len(nw) > 3)
    return [w for w, _ in counts.most_common(top)]


def word_stress(audio: np.ndarray, sr: int, words: list[dict], sentences: list[dict]) -> list[float]:
    """How hard each word is stressed when spoken (0 = not at all, ~1+ = clearly emphasised).

    Combines loudness relative to the rest of the sentence, vowel lengthening
    (duration per character vs the sentence) and a pause right after the word.
    Function words never count as stressed.
    """
    out = [0.0] * len(words)
    for sent in sentences:
        idx = list(range(sent["w0"], sent["w1"]))
        if len(idx) < 3:
            continue
        db, rate = [], []
        for i in idx:
            w = words[i]
            seg = audio[int(w["s"] * sr):max(int(w["s"] * sr) + 1, int(w["e"] * sr))]
            db.append(20 * np.log10(np.sqrt(np.mean(seg ** 2) + 1e-10) + 1e-10) if len(seg) else -80.0)
            rate.append((w["e"] - w["s"]) / max(2, len(norm_word(w["w"]))))
        db_a, rate_a = np.array(db), np.array(rate)
        zdb = (db_a - np.median(db_a)) / (np.std(db_a) + 1.5)
        zrate = (rate_a - np.median(rate_a)) / (np.std(rate_a) + 1e-3)
        for k, i in enumerate(idx):
            tok = norm_word(words[i]["w"])
            if not tok or tok in STOPWORDS or len(tok) < 3 and not tok.isdigit():
                continue
            nxt = words[i + 1]["s"] - words[i]["e"] if i + 1 < len(words) else 0
            s = 0.7 * zdb[k] + 0.35 * zrate[k] + (0.3 if nxt > 0.22 else 0) + (0.25 if tok.isdigit() else 0)
            out[i] = round(float(max(0.0, s)), 3)
    return out


def build_timeline(wav_path: str | Path, transcript: dict, out_path: str | Path) -> dict:
    audio, sr = load_wav_mono(wav_path)
    words = transcript["words"]
    sentences = transcript["sentences"]
    energy = energy_curve(audio, sr)
    speech_energy = energy[energy > np.percentile(energy, 30)] if len(energy) else energy
    base, spread = (float(np.median(speech_energy)), float(np.std(speech_energy) + 1e-6)) if len(speech_energy) else (0, 1)
    laughs = laughter_times(audio, sr, words, energy)
    keywords = set(episode_keywords(words))

    for i, sent in enumerate(sentences):
        ws = words[sent["w0"]:sent["w1"]]
        dur = max(0.3, sent["e"] - sent["s"])
        a, b = int(sent["s"] / HOP_S), max(int(sent["s"] / HOP_S) + 1, int(sent["e"] / HOP_S))
        seg = energy[a:b]
        tokens = [norm_word(w["w"]) for w in ws]
        sent["i"] = i
        sent["rate"] = round(len(ws) / dur, 2)                      # words per second
        sent["energy"] = round(float((seg.mean() - base) / spread) if len(seg) else 0, 2)
        sent["peak"] = round(float((seg.max() - base) / spread) if len(seg) else 0, 2)
        sent["laughs"] = sum(1 for t in laughs if sent["s"] - 0.5 <= t <= sent["e"] + 1.5)
        sent["question"] = sent["text"].rstrip().endswith("?")
        sent["exclaim"] = "!" in sent["text"]
        sent["triggers"] = sum(1 for t in tokens if t in TRIGGERS)
        sent["emotion"] = sum(1 for t in tokens if t in EMOTION) + sum(1 for t in tokens if t in INTENSIFIERS) * 0.5
        sent["keywords"] = sum(1 for t in tokens if t in keywords)
        sent["numbers"] = sum(1 for w in ws if re.search(r"\d", w["w"]))
        sent["weak_open"] = bool(tokens) and tokens[0] in WEAK_OPENERS
        sent["conf"] = round(float(np.mean([w["p"] for w in ws])) if ws else 0, 3)
        nxt = sentences[i + 1]["s"] if i + 1 < len(sentences) else sent["e"]
        sent["gap_after"] = round(max(0.0, nxt - sent["e"]), 2)

    timeline = {
        "duration": len(audio) / sr,
        "hop": HOP_S,
        "energy": [round(float(x), 1) for x in energy[:: 1]],
        "energy_base": base,
        "energy_spread": spread,
        "laughs": laughs,
        "keywords": sorted(keywords),
        "sentences": sentences,
        "language": transcript.get("language"),
        "stress": word_stress(audio, sr, words, sentences),
    }
    Path(out_path).write_text(json.dumps(timeline, ensure_ascii=False))
    return timeline


def sentence_intensity(sent: dict) -> float:
    """A single 0..1 'something is happening here' number for one sentence."""
    x = (0.35 * max(0, sent.get("peak", 0)) + 0.25 * max(0, sent.get("energy", 0))
         + 0.6 * sent.get("laughs", 0) + 0.35 * sent.get("triggers", 0) + 0.3 * sent.get("emotion", 0)
         + 0.25 * sent.get("question", False) + 0.2 * sent.get("exclaim", False) + 0.15 * sent.get("numbers", 0))
    return 1 - math.exp(-x / 2.0)
