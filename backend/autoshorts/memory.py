"""What the worker holds in memory, and freeing it.

Models load on demand and are released after each stage, but the LLM, Whisper and voice models stay
cached between jobs so the next one starts fast. On a laptop that costs RAM and battery, so they are
also dropped after a few idle minutes (`unload_idle_minutes`), or right away from the dashboard.
"""

from __future__ import annotations

import gc
import os
import sys

from . import logs

log = logs.get("memory")


def loaded() -> list[str]:
    out = []
    m = sys.modules
    llm = m.get("autoshorts.llm")
    if llm and llm._state.get("model") is not None:
        out.append(f"LLM ({str(llm._state['name']).split('/')[-1]})")
    for mod in ("autoshorts.voices", "autoshorts.voices_torch"):
        v = m.get(mod)
        if v and v._loaded.get("model") is not None:
            out.append(f"voice ({v._loaded['engine']})")
    mw = m.get("mlx_whisper.transcribe")
    tr = m.get("autoshorts.understand.transcribe")
    if (mw and getattr(mw.ModelHolder, "model", None) is not None) or (tr and tr._fw_cache):
        out.append("Whisper")
    al = m.get("autoshorts.understand.align")
    if al and al._cache:
        out.append("word aligner")
    return out


def rss_mb() -> float:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss / 1e6
    except Exception:
        return 0.0


def free_all() -> dict:
    """Drop every cached model. Only call when no job is using them (the worker checks).

    Only touches libraries that are already imported: importing torch or MLX just to clear their caches
    would cost more memory than it frees."""
    before = rss_mb()
    names = loaded()
    m = sys.modules
    if "autoshorts.llm" in m:
        m["autoshorts.llm"].unload()
    if "autoshorts.voices" in m:
        m["autoshorts.voices"].unload()
    if "autoshorts.voices_torch" in m:
        m["autoshorts.voices_torch"].unload()
    if "autoshorts.understand.transcribe" in m:
        m["autoshorts.understand.transcribe"]._release()
    if "autoshorts.understand.align" in m:
        m["autoshorts.understand.align"]._cache.clear()
    gc.collect()
    if "mlx.core" in m:
        try:
            m["mlx.core"].clear_cache()
        except Exception:  # noqa: S110 - best-effort cleanup, nothing to recover
            pass
    if "torch" in m:
        torch = m["torch"]
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:  # noqa: S110 - best-effort cleanup, nothing to recover
            pass
    after = rss_mb()
    log.info("freed models %s: %.0f MB -> %.0f MB", names or "(none)", before, after)
    return {"freed": names, "before_mb": round(before), "after_mb": round(after)}
