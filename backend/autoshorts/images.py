"""Local image generation (Z-Image-Turbo) for faceless scenes and B-roll.

  mlx    Apple Silicon: mflux, 4-bit
  torch  Windows / Linux: diffusers ZImagePipeline on CUDA (CPU works but takes minutes per image)

The model is loaded for a batch of images and released right after, so it never sits in memory.
"""

from __future__ import annotations

import gc
import time
from pathlib import Path
from typing import Callable

from . import logs, settings

log = logs.get("images")

MLX_REPO = "filipstrand/Z-Image-Turbo-mflux-4bit"
TORCH_REPO = "Tongyi-MAI/Z-Image-Turbo"


def backend() -> str:
    import os

    forced = os.environ.get("AUTOSHORTS_IMAGES", "").lower()
    if forced in ("mlx", "torch"):
        return forced
    try:
        import mflux  # noqa: F401
        return "mlx"
    except ImportError:
        return "torch"


def _size(fast: bool) -> tuple[int, int, int]:
    return (576, 1024, 6) if fast else (768, 1344, 8)


def generate(jobs: list[tuple[str, Path, int]], progress: Callable[[float, str], None] | None = None,
             label: str = "shot", on_done: Callable[[int], None] | None = None) -> list[Path]:
    """Draw each (prompt, target, seed). Near-black results (a failed sample) are retried once."""
    import cv2

    if not jobs:
        return []
    progress = progress or (lambda f, m: None)
    fast = settings.get("faceless_quality") == "fast"
    w, h, steps = _size(fast)
    draw, release = _mlx() if backend() == "mlx" else _torch()
    out = []
    try:
        for j, (prompt, target, seed) in enumerate(jobs):
            progress(j / len(jobs), f"drawing {label} {j + 1}/{len(jobs)}")
            t0 = time.time()
            draw(prompt, target, seed, w, h, steps)
            arr = cv2.imread(str(target))
            if arr is not None and float(arr.mean()) < 20:
                draw(prompt, target, seed + 101, w, h, steps)
            log.info("drew %s in %.0fs", Path(target).name, time.time() - t0)
            out.append(Path(target))
            if on_done:
                on_done(j)
    finally:
        release()
    return out


def _mlx():
    from huggingface_hub import snapshot_download
    from mflux.models.z_image.variants.z_image import ZImage

    mx = None
    try:
        import mlx.core as mx

        # MLX keeps freed buffers around by default; on a 16 GB Mac that pushes the model into swap.
        mx.set_cache_limit(512 * 1024 * 1024)
    except Exception:  # noqa: BLE001
        pass
    model = ZImage(quantize=4, model_path=snapshot_download(MLX_REPO))

    def draw(prompt, target, seed, w, h, steps):
        img = model.generate_image(seed=seed, prompt=prompt, num_inference_steps=steps, width=w, height=h)
        img.save(path=str(target)) if hasattr(img, "save") else img.image.save(target)
        if mx is not None:
            mx.clear_cache()

    def release():
        nonlocal model
        model = None
        gc.collect()
        if mx is not None:
            mx.clear_cache()

    return draw, release


def _torch():
    import torch
    from diffusers import ZImagePipeline

    cuda = torch.cuda.is_available()
    pipe = ZImagePipeline.from_pretrained(TORCH_REPO, torch_dtype=torch.bfloat16 if cuda else torch.float32)
    if cuda:
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        # Under ~16 GB of VRAM, stream weights to the GPU part by part instead of holding them all.
        pipe.enable_model_cpu_offload() if vram < 16 else pipe.to("cuda")
    else:
        log.warning("no CUDA GPU: drawing on the CPU takes several minutes per image (set Faceless quality to fast)")

    def draw(prompt, target, seed, w, h, steps):
        gen = torch.Generator("cuda" if cuda else "cpu").manual_seed(seed)
        img = pipe(prompt, height=h, width=w, num_inference_steps=steps, guidance_scale=0.0, generator=gen).images[0]
        img.save(target)

    def release():
        nonlocal pipe
        pipe = None
        gc.collect()
        if cuda:
            torch.cuda.empty_cache()

    return draw, release
