"""Local voices for faceless videos and narrated intros.

Engines (all on-device through mlx-audio):
  kokoro      fast and light; also the Hindi voices
  qwen        Qwen3-TTS CustomVoice: natural preset speakers + a style instruction (storyteller, hype, ...)
  design      Qwen3-TTS VoiceDesign: the voice is described in words ("deep, gravelly documentary narrator")
  chatterbox  Chatterbox Turbo: expressive English with an intensity dial; can clone a voice from a short sample

Voice ids look like "qwen:Ryan:storyteller", "design:documentary", "kokoro:am_michael", "chatterbox:clone:<file>".
"""

from __future__ import annotations

import gc
import re
import wave
from pathlib import Path

import numpy as np

from . import logs
from .config import paths

log = logs.get("voices")

MODELS = {
    "kokoro": "mlx-community/Kokoro-82M-bf16",
    "qwen": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-bf16",
    "design": "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
    "chatterbox": "mlx-community/chatterbox-turbo-fp16",
    "clone": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16",   # speaks in the voice of a reference clip
}

# VoiceDesign invents a voice from its description on every call, so reading scene by scene gives a slightly
# different narrator each time. We design each voice ONCE (a reference clip, kept on disk) and clone it for every
# line, so the narrator is identical across scenes and across videos.
REFERENCE_TEXT = ("Thirty-one thousand years ago, in a cave deep in the rainforest, someone did something nobody "
                  "thought was possible. And for a very long time, nobody knew.")
LOCK_VERSION = "lock1"

STYLES = {  # delivery instructions for Qwen3-TTS CustomVoice
    "storyteller": "Tell this like a gripping story: warm, intimate, building suspense, slight pauses before reveals.",
    "hype": "High energy and punchy, like a viral short-form creator. Fast pace, emphasise the key words, excited.",
    "documentary": "Deep, calm authority like a documentary narrator. Measured pace, confident, cinematic.",
    "conversational": "Casual and friendly, like talking to a friend. Natural rhythm, a little smile in the voice.",
    "dramatic": "Dramatic and intense, like a movie trailer. Low and powerful, strong pauses, emotional peaks.",
}

DESIGNS = {  # voice descriptions for Qwen3-TTS VoiceDesign
    "documentary": "A deep, slightly gravelly male narrator in his 40s, calm and authoritative, cinematic documentary delivery.",
    "storyteller_f": "A warm female storyteller in her 30s, intimate and expressive, builds suspense with gentle pauses.",
    "hype_m": "An energetic young male creator, upbeat and punchy, speaks fast with excitement and emphasis.",
    "mystery": "A hushed, mysterious male voice, low and breathy, as if revealing a secret, slow and tense.",
    "coach": "A confident, motivational male coach, strong and direct, raises intensity on key lines.",
    "news_f": "A crisp, clear female news anchor, neutral accent, confident and engaging.",
}

KOKORO = {
    "am_michael": "US male (deep)", "am_adam": "US male", "af_heart": "US female (warm)", "af_bella": "US female (energetic)",
    "bm_george": "UK male", "bf_emma": "UK female", "hm_omega": "Hindi male", "hf_alpha": "Hindi female",
}

DEFAULT_QWEN_SPEAKERS = ["Ryan", "Aiden", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ono_Anna", "Sohee"]

_loaded: dict = {"engine": None, "model": None}


def catalog() -> list[dict]:
    """Every voice the UI can offer, best first."""
    out: list[dict] = []
    for key, desc in DESIGNS.items():
        out.append({"id": f"design:{key}", "engine": "design", "label": key.replace("_", " ").title(),
                    "note": desc, "group": "Designed voices (most natural)"})
    for spk in qwen_speakers():
        for style in ("storyteller", "hype", "documentary", "dramatic", "conversational"):
            out.append({"id": f"qwen:{spk}:{style}", "engine": "qwen", "label": f"{spk.replace('_', ' ').title()} · {style}",
                        "note": STYLES[style], "group": "Natural speakers + delivery style"})
    out.append({"id": "chatterbox:default:0.6", "engine": "chatterbox", "label": "Expressive (Chatterbox)",
                "note": "Very expressive English delivery", "group": "Expressive"})
    for f in sorted((paths().home / "voices").glob("*.wav")) if (paths().home / "voices").exists() else []:
        out.append({"id": f"chatterbox:clone:{f.name}", "engine": "chatterbox", "label": f"Clone: {f.stem}",
                    "note": "Your uploaded voice sample", "group": "Your voices"})
    for k, v in KOKORO.items():
        out.append({"id": f"kokoro:{k}", "engine": "kokoro", "label": v, "note": "Fast, lighter quality",
                    "group": "Fast (Kokoro)"})
    return out


def qwen_speakers() -> list[str]:
    try:
        import json

        from huggingface_hub import snapshot_download

        p = Path(snapshot_download(MODELS["qwen"], local_files_only=True)) / "config.json"
        cfg = json.loads(p.read_text())
        spk = cfg.get("talker_config", {}).get("spk_id") or cfg.get("spk_id") or {}
        names = [n for n in spk] if isinstance(spk, dict) else []
        return [n for n in names if n] or DEFAULT_QWEN_SPEAKERS
    except Exception:  # noqa: BLE001
        return DEFAULT_QWEN_SPEAKERS


def _model(engine: str):
    if _loaded["engine"] == engine:
        return _loaded["model"]
    unload()
    from mlx_audio.tts.utils import load_model

    log.info("loading voice model %s", MODELS[engine])
    _loaded.update(engine=engine, model=load_model(MODELS[engine]))
    return _loaded["model"]


def unload() -> None:
    if _loaded["model"] is not None:
        _loaded.update(engine=None, model=None)
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass


def design_reference(key: str) -> tuple[Path, str]:
    """The saved reference clip for a designed voice (made on first use)."""
    d = paths().home / "voices" / "designed"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", key)[:60]
    wav = d / f"{safe}.wav"
    if not wav.exists():
        model = _model("design")
        sr = int(getattr(model, "sample_rate", 24000) or 24000)
        chunks = [np.array(r.audio, dtype=np.float32) for r in
                  model.generate(text=REFERENCE_TEXT, instruct=DESIGNS.get(key, key), lang_code="auto")]
        audio = np.concatenate(chunks)
        nz = np.where(np.abs(audio) > 0.01)[0]
        if len(nz):
            audio = audio[max(0, nz[0] - int(0.05 * sr)): nz[-1] + int(0.2 * sr)]
        save_wav(audio, sr, wav)
        log.info("designed voice %s saved as %s (%.1fs)", key, wav.name, len(audio) / sr)
    return wav, REFERENCE_TEXT


def cache_tag(voice: str) -> str:
    """Part of the cache key for a voice line: changes when the way a voice is produced changes."""
    voice = normalize_id(voice)
    return f"{voice}#{LOCK_VERSION}" if voice.startswith("design:") else voice


def normalize_id(voice: str) -> str:
    """Old ids ('am_michael') map to Kokoro."""
    return voice if ":" in voice else f"kokoro:{voice}"


def synth(text: str, voice: str, speed: float = 1.0) -> tuple[np.ndarray, int]:
    voice = normalize_id(voice)
    parts = voice.split(":")
    engine = parts[0]
    model = _model(engine)
    sr = int(getattr(model, "sample_rate", 24000) or 24000)
    kw: dict = {}
    if engine == "kokoro":
        kid = parts[1]
        kw = dict(voice=kid, speed=speed, lang_code=kid[0] if kid[0] in "abhefijpz" else "a")
    elif engine == "qwen":
        kw = dict(voice=parts[1], instruct=STYLES.get(parts[2] if len(parts) > 2 else "storyteller"), lang_code="auto")
    elif engine == "design":
        try:
            ref, ref_text = design_reference(parts[1])
            model = _model("clone")
            sr = int(getattr(model, "sample_rate", 24000) or 24000)
            kw = dict(ref_audio=str(ref), ref_text=ref_text, lang_code="auto")
        except Exception as exc:  # noqa: BLE001 - no clone model (offline): fall back to designing per line
            log.warning("voice lock unavailable (%s); designed voice may vary between lines", exc)
            model = _model("design")
            kw = dict(instruct=DESIGNS.get(parts[1], parts[1]), lang_code="auto")
    elif engine == "chatterbox":
        if parts[1] == "clone" and len(parts) > 2:
            kw["ref_audio"] = str(paths().home / "voices" / parts[2])
        kw["exaggeration"] = float(parts[2]) if parts[1] != "clone" and len(parts) > 2 else 0.6
    chunks = [np.array(r.audio, dtype=np.float32) for r in model.generate(text=text, **kw)]
    audio = np.concatenate(chunks) if chunks else np.zeros(sr // 2, np.float32)
    return audio, sr


def save_wav(audio: np.ndarray, sr: int, out: Path) -> float:
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return len(audio) / sr


def speak_to_file(text: str, voice: str, out: Path, speed: float = 1.0) -> float:
    audio, sr = synth(text, voice, speed)
    # trim leading/trailing silence so scenes flow
    thr = 0.01
    nz = np.where(np.abs(audio) > thr)[0]
    if len(nz):
        audio = audio[max(0, nz[0] - int(0.03 * sr)): nz[-1] + int(0.12 * sr)]
    return save_wav(audio, sr, out)


def preview(voice: str, text: str = "") -> Path:
    text = text or "Thirty-one thousand years ago, someone did the impossible. And nobody knew, until now."
    d = paths().tmp / "voice_previews"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", voice)
    out = d / f"{safe}.wav"
    if not out.exists():
        speak_to_file(text, voice, out)
    return out
