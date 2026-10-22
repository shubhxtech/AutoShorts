"""Word-level transcription with Whisper, on-device.

Uses mlx-whisper on Apple silicon (large-v3-turbo by default) and falls back to
faster-whisper elsewhere. Long episodes are split at quiet points into ~10 min
chunks so progress can be reported and memory stays flat.
"""

from __future__ import annotations

import gc
import inspect
import json
import re
from pathlib import Path
from typing import Callable

import numpy as np

from .. import logs, settings
from ..media import load_wav_mono

log = logs.get("transcribe")

CHUNK_S = 600
SENT_END = re.compile(r"[.!?…।॥]+[\"')\]]*$")


def _split_points(audio: np.ndarray, sr: int, chunk_s: float = CHUNK_S) -> list[float]:
    """Chunk boundaries placed at the quietest 0.5 s window near each multiple of chunk_s."""
    total = len(audio) / sr
    points = [0.0]
    t = chunk_s
    hop = int(sr * 0.05)
    while t < total - 30:
        lo, hi = int((t - 20) * sr), int(min(total, t + 20) * sr)
        seg = audio[lo:hi]
        if len(seg) < hop * 10:
            break
        frames = seg[: len(seg) // hop * hop].reshape(-1, hop)
        rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
        win = np.convolve(rms, np.ones(10) / 10, mode="valid")
        best = int(np.argmin(win)) + 5
        points.append((lo + best * hop) / sr)
        t = points[-1] + chunk_s
    points.append(total)
    return points


def _mlx_transcribe(chunk: np.ndarray, model: str, language: str | None) -> dict:
    import mlx_whisper

    # A punctuated prompt keeps Whisper writing punctuation on fast, run-on speech.
    kwargs = dict(path_or_hf_repo=model, word_timestamps=True, verbose=None, language=language,
                  condition_on_previous_text=False,
                  initial_prompt="Hello, and welcome back. Today we're talking about it. Right? Yes! Let's go.")
    params = inspect.signature(mlx_whisper.transcribe).parameters
    if "hallucination_silence_threshold" in params:
        kwargs["hallucination_silence_threshold"] = 2.0
    return mlx_whisper.transcribe(chunk, **kwargs)


_fw_cache: dict = {}


def _fw_model(name: str):
    """faster-whisper on the GPU when there is one (CUDA, float16), otherwise CPU int8."""
    from faster_whisper import WhisperModel

    if name not in _fw_cache:
        try:
            import ctranslate2

            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:  # noqa: BLE001
            cuda = False
        _fw_cache.clear()
        _fw_cache[name] = WhisperModel(name, device="cuda" if cuda else "cpu",
                                       compute_type="float16" if cuda else "int8")
    return _fw_cache[name]


def _faster_transcribe(chunk: np.ndarray, model: str, language: str | None) -> dict:
    from faster_whisper import WhisperModel

    name = "large-v3-turbo" if "turbo" in model else "small"
    wm = _fw_model(name)
    segments, info = wm.transcribe(chunk, word_timestamps=True, language=language, condition_on_previous_text=False)
    segs = []
    for s in segments:
        segs.append({"start": s.start, "end": s.end, "text": s.text,
                     "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                               for w in (s.words or [])]})
    return {"language": info.language, "segments": segs}


def _engine():
    try:
        import mlx_whisper  # noqa: F401
        return _mlx_transcribe
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401
        return _faster_transcribe
    except ImportError as exc:
        raise RuntimeError("No Whisper engine installed (mlx-whisper on Apple silicon, or faster-whisper)") from exc


def build_sentences(words: list[dict], max_words: int = 40, pause_s: float = 0.9) -> list[dict]:
    """Split on punctuation; for run-on stretches without it, split on the longest pauses instead."""
    sentences, start = [], 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        gap = (words[i + 1]["s"] - w["e"]) if not last else 0
        run = i - start + 1
        # In unpunctuated speech, a 0.35 s+ pause after 12+ words is almost always a sentence break.
        soft = run >= 12 and gap > 0.35 and not any(SENT_END.search(x["w"]) for x in words[start:i])
        if last or w.get("eos") or SENT_END.search(w["w"]) or gap > pause_s or soft or run >= max_words:
            chunk = words[start:i + 1]
            sentences.append({"s": chunk[0]["s"], "e": chunk[-1]["e"], "w0": start, "w1": i + 1,
                              "text": " ".join(x["w"] for x in chunk).strip(), "speaker": None})
            start = i + 1
    return sentences


# Languages Whisper confuses with Hindi (same spoken language, different script).
HINDI_LIKE = {"ur", "pa", "sd", "ne", "mr", "bn", "gu", "sa", "bho"}


def allowed_language(detected: str | None) -> str:
    allowed = [l for l in (settings.get("allowed_languages") or []) if l] or ["en", "hi"]
    if not detected or detected in allowed:
        return detected or allowed[0]
    if detected in HINDI_LIKE and "hi" in allowed:
        return "hi"
    return "en" if "en" in allowed else allowed[0]


def transcribe(wav_path: str | Path, out_path: str | Path, language: str | None = None,
               progress: Callable[[float, str], None] | None = None) -> dict:
    audio, sr = load_wav_mono(wav_path)
    if sr != 16000:
        raise ValueError("expected 16 kHz audio")
    model = settings.get("whisper_model")
    engine = _engine()
    points = _split_points(audio, sr)
    words: list[dict] = []
    detected = language
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        if progress:
            progress(k / (len(points) - 1), f"transcribing {a / 60:.0f}–{b / 60:.0f} min")
        chunk = audio[int(a * sr):int(b * sr)]
        res = engine(chunk, model, detected if detected and detected != "auto" else None)
        if not detected or detected == "auto":
            raw = res.get("language") or detected
            detected = allowed_language(raw)
            if detected != raw:
                log.info("detected %s; transcribing as %s (allowed: %s)", raw, detected, settings.get("allowed_languages"))
                res = engine(chunk, model, detected)
        for seg in res.get("segments", []):
            for w in seg.get("words") or []:
                text = (w.get("word") or "").strip()
                if not text:
                    continue
                s, e = float(w["start"]) + a, float(w["end"]) + a
                if words and s < words[-1]["s"]:
                    continue  # overlap at chunk boundary
                words.append({"w": text, "s": round(s, 3), "e": round(max(e, s + 0.04), 3),
                              "p": round(float(w.get("probability", 1.0)), 3)})
    _drop_repetition_loops(words)
    data = {"language": detected or "en", "duration": len(audio) / sr, "words": words,
            "sentences": build_sentences(words)}
    Path(out_path).write_text(json.dumps(data, ensure_ascii=False))
    _release()
    if progress:
        progress(1.0, f"transcribed {len(words)} words")
    return data


def _drop_repetition_loops(words: list[dict], window: int = 6, repeats: int = 3) -> None:
    """Remove Whisper hallucination loops (the same short phrase repeated many times)."""
    i = 0
    while i + window * repeats <= len(words):
        phrase = [w["w"].lower() for w in words[i:i + window]]
        same = 1
        j = i + window
        while j + window <= len(words) and [w["w"].lower() for w in words[j:j + window]] == phrase:
            same += 1
            j += window
        if same >= repeats:
            del words[i + window:j]
        i += 1


def _release() -> None:
    """Free Whisper's weights so the LLM and renderer get the RAM back."""
    try:
        from mlx_whisper.transcribe import ModelHolder

        ModelHolder.model = None
        ModelHolder.model_path = None
    except ImportError:
        pass
    _fw_cache.clear()
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except Exception:  # noqa: BLE001
        pass


def diarize(wav_path: str | Path, data: dict) -> dict:
    """Optional speaker labels via pyannote (needs a Hugging Face token)."""
    token = settings.get("hf_token")
    if not settings.get("diarization") or not token:
        return data
    try:
        from pyannote.audio import Pipeline
    except ImportError:
        log.info("pyannote not installed; skipping diarization")
        return data
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=token)
    try:
        import torch

        if torch.backends.mps.is_available():
            pipe.to(torch.device("mps"))
    except Exception:  # noqa: BLE001
        pass
    diar = pipe(str(wav_path))
    turns = [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
    for sent in data["sentences"]:
        overlap: dict[str, float] = {}
        for s, e, spk in turns:
            o = min(e, sent["e"]) - max(s, sent["s"])
            if o > 0:
                overlap[spk] = overlap.get(spk, 0) + o
        sent["speaker"] = max(overlap, key=overlap.get) if overlap else None
    data["speakers"] = sorted({t[2] for t in turns})
    return data
