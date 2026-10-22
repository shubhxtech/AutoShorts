"""Forced alignment: snap every Whisper word to where it is actually spoken.

Whisper's word timestamps come from attention and can drift by a few hundred
milliseconds, which shows up as captions landing early/late and cuts that clip
a syllable. Like WhisperX, we re-time words with a CTC phoneme/character model
(wav2vec2) and Viterbi alignment. This runs locally (transformers + torch,
MPS when available). Words that can't be aligned (numbers, symbols) or whose
new time is implausible keep Whisper's timing.
"""

from __future__ import annotations

import re

import numpy as np

from .. import logs

log = logs.get("align")
MODEL = "facebook/wav2vec2-base-960h"   # English characters; 95M params
_cache: dict = {}


def _load():
    if "model" not in _cache:
        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        proc = Wav2Vec2Processor.from_pretrained(MODEL)
        model = Wav2Vec2ForCTC.from_pretrained(MODEL).eval()
        dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        model.to(dev)
        _cache.update(model=model, proc=proc, dev=dev, vocab=proc.tokenizer.get_vocab())
    return _cache["model"], _cache["proc"], _cache["dev"], _cache["vocab"]


def unload() -> None:
    _cache.clear()
    import gc

    gc.collect()


def _emissions(audio: np.ndarray, sr: int) -> np.ndarray:
    import torch

    model, proc, dev, _ = _load()
    with torch.inference_mode():
        x = proc(audio, sampling_rate=sr, return_tensors="pt").input_values.to(dev)
        logits = model(x).logits[0]
        return torch.log_softmax(logits, dim=-1).float().cpu().numpy()


def _viterbi(em: np.ndarray, tokens: list[int], blank: int = 0) -> list[tuple[int, int]] | None:
    """CTC trellis + backtrack. Returns (token index, frame) pairs for the best path."""
    T, N = em.shape[0], len(tokens)
    if N == 0 or T < N:
        return None
    trellis = np.full((T + 1, N + 1), -np.inf, dtype=np.float32)
    trellis[0, 0] = 0
    trellis[1:, 0] = np.cumsum(em[:, blank])
    tok = np.array(tokens)
    for t in range(T):
        stay = trellis[t, 1:] + em[t, blank]
        move = trellis[t, :-1] + em[t, tok]
        trellis[t + 1, 1:] = np.maximum(stay, move)
    # backtrack
    path = []
    j = N
    t = T
    while j > 0 and t > 0:
        stayed = trellis[t - 1, j] + em[t - 1, blank]
        changed = trellis[t - 1, j - 1] + em[t - 1, tok[j - 1]]
        if changed > stayed:
            path.append((j - 1, t - 1))
            j -= 1
        t -= 1
    if j > 0:
        return None
    return path[::-1]


def _norm(w: str) -> str:
    return re.sub(r"[^A-Z']", "", w.upper())


def align_ranges(wav_path, transcript_path, ranges: list[tuple[float, float]], language: str = "en") -> dict:
    """Re-time only the words inside the chosen clips (fast) and save them back into the transcript."""
    import json
    from pathlib import Path

    from ..media import load_wav_mono

    data = json.loads(Path(transcript_path).read_text())
    words = data["words"]
    audio, sr = load_wav_mono(wav_path)
    total = {"aligned": 0, "kept": 0, "shift_ms": []}
    for a, b in ranges:
        idx = [k for k, w in enumerate(words) if w["s"] >= a - 1.0 and w["e"] <= b + 1.0]
        if not idx:
            continue
        sub, st = align_words(audio, sr, [words[k] for k in idx], language)
        for k, w in zip(idx, sub):
            words[k] = w
        total["aligned"] += st["aligned"]
        total["kept"] += st["kept"]
        if st["shift_ms"]:
            total["shift_ms"].append(st["shift_ms"])
    data["words"] = words
    data["aligned"] = True
    Path(transcript_path).write_text(json.dumps(data, ensure_ascii=False))
    unload()
    total["shift_ms"] = round(sum(total["shift_ms"]) / len(total["shift_ms"]), 1) if total["shift_ms"] else 0
    return total


def align_words(audio: np.ndarray, sr: int, words: list[dict], language: str = "en",
                window_s: float = 18.0, max_shift: float = 0.6) -> tuple[list[dict], dict]:
    """Return re-timed words (same order/length) and stats. Only English is aligned for now."""
    stats = {"aligned": 0, "kept": 0, "shift_ms": 0.0}
    if not (language or "en").startswith("en") or not words:
        return words, stats
    try:
        _, _, _, vocab = _load()
    except Exception as exc:  # noqa: BLE001
        log.info("aligner unavailable (%s); keeping Whisper timings", exc)
        return words, stats
    delim = vocab.get("|", 4)
    out = [dict(w) for w in words]
    i = 0
    shifts = []
    while i < len(words):
        # window of consecutive words
        j = i
        while j + 1 < len(words) and words[j + 1]["e"] - words[i]["s"] < window_s:
            j += 1
        a = max(0.0, words[i]["s"] - 0.25)
        b = words[j]["e"] + 0.25
        seg = audio[int(a * sr):int(b * sr)]
        idx = [k for k in range(i, j + 1) if _norm(words[k]["w"])]
        tokens, owner = [], []
        for k in idx:
            for ch in _norm(words[k]["w"]):
                if ch in vocab:
                    tokens.append(vocab[ch])
                    owner.append(k)
            tokens.append(delim)
            owner.append(-1)
        if tokens and tokens[-1] == delim:
            tokens.pop()
            owner.pop()
        try:
            em = _emissions(seg, sr) if len(seg) > sr * 0.2 else None
            path = _viterbi(em, tokens, blank=vocab.get("<pad>", 0)) if em is not None else None
        except Exception as exc:  # noqa: BLE001
            log.debug("alignment failed for words %d-%d: %s", i, j, exc)
            path = None
        if path:
            fps = em.shape[0] / max(1e-6, (len(seg) / sr))
            first: dict[int, int] = {}
            last: dict[int, int] = {}
            for tix, frame in path:
                k = owner[tix]
                if k < 0:
                    continue
                first.setdefault(k, frame)
                last[k] = frame
            for k in idx:
                if k not in first:
                    stats["kept"] += 1
                    continue
                s = a + first[k] / fps
                e = a + (last[k] + 1) / fps
                if abs(s - words[k]["s"]) <= max_shift and e > s:
                    shifts.append(abs(s - words[k]["s"]))
                    out[k]["s"], out[k]["e"] = round(s, 3), round(max(e, s + 0.05), 3)
                    stats["aligned"] += 1
                else:
                    stats["kept"] += 1
        else:
            stats["kept"] += len(idx)
        i = j + 1
    # keep words ordered and non-overlapping
    for k in range(1, len(out)):
        if out[k]["s"] < out[k - 1]["e"]:
            out[k - 1]["e"] = max(out[k - 1]["s"] + 0.03, out[k]["s"])
    stats["shift_ms"] = round(float(np.mean(shifts)) * 1000, 1) if shifts else 0.0
    return out, stats
