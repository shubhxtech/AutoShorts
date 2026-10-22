"""PyTorch voice engines for Windows and Linux (CUDA when available, CPU otherwise).

Same voice ids as the MLX engines in voices.py, so projects and settings move between machines:
  kokoro:<voice>            kokoro package (light; runs fine on CPU)
  qwen:<speaker>:<style>    qwen-tts CustomVoice
  design:<key>              qwen-tts VoiceDesign once -> saved reference -> qwen-tts Base clones it per line
  chatterbox:...            chatterbox-tts (Turbo), optional clone from an uploaded sample
"""

from __future__ import annotations

import gc

import numpy as np

from . import logs

log = logs.get("voices")

REPOS = {
    "qwen": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}

_loaded: dict = {"engine": None, "model": None}


def available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def _device() -> tuple[str, object]:
    import torch

    if torch.cuda.is_available():
        return "cuda:0", torch.bfloat16
    return "cpu", torch.float32


def unload() -> None:
    if _loaded["model"] is not None:
        _loaded.update(engine=None, model=None)
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def _model(engine: str):
    if _loaded["engine"] == engine:
        return _loaded["model"]
    unload()
    device, dtype = _device()
    log.info("loading voice model %s on %s", engine, device)
    if engine == "kokoro":
        from kokoro import KPipeline

        model = {}                                    # one pipeline per language code, made on demand
    elif engine in REPOS:
        from qwen_tts import Qwen3TTSModel

        model = Qwen3TTSModel.from_pretrained(REPOS[engine], device_map=device, dtype=dtype)
    elif engine == "chatterbox":
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        model = ChatterboxTurboTTS.from_pretrained(device=device.split(":")[0])
    else:
        raise ValueError(f"unknown voice engine {engine}")
    _loaded.update(engine=engine, model=model)
    return model


def _kokoro(text: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
    from kokoro import KPipeline

    pipes = _model("kokoro")
    lang = voice[0] if voice[0] in "abhefijpz" else "a"
    if lang not in pipes:
        pipes[lang] = KPipeline(lang_code=lang)
    parts = [r.audio.cpu().numpy().astype(np.float32) for r in pipes[lang](text, voice=voice, speed=speed)
             if r.audio is not None]
    return (np.concatenate(parts) if parts else np.zeros(12000, np.float32)), 24000


def synth(text: str, parts: list[str], speed: float, styles: dict, designs: dict, reference) -> tuple[np.ndarray, int]:
    """`parts` is the split voice id. `reference(key)` returns (wav_path, text) for a designed voice."""
    engine = parts[0]
    if engine == "kokoro":
        return _kokoro(text, parts[1], speed)
    if engine == "qwen":
        m = _model("qwen")
        wavs, sr = m.generate_custom_voice(text=text, speaker=parts[1],
                                           instruct=styles.get(parts[2] if len(parts) > 2 else "storyteller"))
        return np.asarray(wavs[0], np.float32), int(sr)
    if engine == "design":
        ref, ref_text = reference(parts[1])
        m = _model("clone")
        wavs, sr = m.generate_voice_clone(text=text, ref_audio=str(ref), ref_text=ref_text)
        return np.asarray(wavs[0], np.float32), int(sr)
    if engine == "chatterbox":
        from .config import paths

        m = _model("chatterbox")
        kw = {}
        if parts[1] == "clone" and len(parts) > 2:
            kw["audio_prompt_path"] = str(paths().home / "voices" / parts[2])
        wav = m.generate(text, **kw)
        return wav.squeeze().cpu().numpy().astype(np.float32), int(m.sr)
    raise ValueError(f"unknown voice engine {engine}")


def design_reference_audio(text: str, description: str) -> tuple[np.ndarray, int]:
    m = _model("design")
    wavs, sr = m.generate_voice_design(text=text, instruct=description)
    return np.asarray(wavs[0], np.float32), int(sr)
