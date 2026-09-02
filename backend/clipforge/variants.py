"""Pick 2–4 distinct variants per moment: different hook, caption style, length or opening."""

from __future__ import annotations

import random

from . import settings
from .analytics import bandit
from .models import Moment
from .render.compositor import RenderSpec


def plan_variants(moment: Moment, n: int | None = None) -> list[RenderSpec]:
    n = int(n or settings.get("variants_per_moment") or 3)
    rng = random.Random(moment.id or 0)
    hooks = moment.hooks or [{"text": moment.title, "style": moment.hook_style or "bold_claim"}]
    has_tight = (moment.tight_start_s is not None and moment.tight_end_s is not None and
                 (moment.end_s - moment.start_s) - (moment.tight_end_s - moment.tight_start_s) >= 3.0)
    lengths = ["full", "tight"] if has_tight else ["full"]
    openings = ["straight", "cold_open"] if moment.punchline_s else ["straight"]
    if moment.punchline_s and float((moment.scores or {}).get("hook", 10)) < 6:
        openings = ["cold_open"]          # weak first line: always open on the strongest one
    if settings.get("narrated_intros"):
        openings.append("narrated")
    hook_styles = sorted({h.get("style", "bold_claim") for h in hooks})

    def hook_for(style: str, used: set[str]) -> str:
        for h in hooks:
            if h.get("style") == style and h["text"] not in used:
                return h["text"]
        for h in hooks:
            if h["text"] not in used:
                return h["text"]
        return hooks[0]["text"]

    forced = settings.get("edit_preset") or "auto"
    presets = [forced] if forced in PRESET_CAPTIONS else list(PRESET_CAPTIONS)

    specs: list[RenderSpec] = []
    used_hooks: set[str] = set()
    seen: set[tuple] = set()
    for k in range(n * 6):
        if len(specs) >= n:
            break
        pick = bandit.best if k == 0 else (lambda d, arms: bandit.sample(d, arms, rng=rng))
        preset = pick("edit_preset", presets)
        style = pick("hook_style", hook_styles)
        cap = pick("caption_style", (moment.meta or {}).get("caption_styles") or PRESET_CAPTIONS[preset])
        length = pick("length_bucket", lengths)
        opening = pick("opening", openings)
        hook = hook_for(style, used_hooks)
        key = (hook, cap, length, opening, preset)
        if key in seen:
            continue
        seen.add(key)
        used_hooks.add(hook)
        specs.append(RenderSpec(hook_text=hook, hook_style=style, caption_style=cap, preset=preset, length=length,
                                opening=opening))
    return specs


# Caption styles that suit each edit preset.
PRESET_CAPTIONS = {
    "clean": ["clean", "boxed", "karaoke"],
    "hype": ["bold", "pop"],
    "minimal": ["minimal", "clean"],
    "explainer": ["chip", "clean"],
}
