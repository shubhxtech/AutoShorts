"""Hinglish captions: romanise Devanagari words one-for-one, keeping word timings."""

from __future__ import annotations

import re

from pydantic import BaseModel

from .. import llm, logs

log = logs.get("script")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class Romanised(BaseModel):
    words: list[str]


SYSTEM = ("You romanise Hindi for social-media captions the way Indian creators write Hinglish "
          "(e.g. 'मैंने सोचा' -> 'maine socha'). English words stay as they are. Keep punctuation.")


def romanise_words(words: list[dict], batch: int = 60) -> list[dict]:
    if not any(DEVANAGARI.search(w["w"]) for w in words) or not llm.available():
        return words
    out = [dict(w) for w in words]
    for i in range(0, len(words), batch):
        chunk = [w["w"] for w in words[i:i + batch]]
        if not any(DEVANAGARI.search(x) for x in chunk):
            continue
        prompt = ("Romanise each item. Return exactly the same number of items, in order.\n"
                  + "\n".join(f"{k + 1}. {x}" for k, x in enumerate(chunk)))
        try:
            res = llm.structured(Romanised, SYSTEM, prompt, max_tokens=1200, temperature=0.1)
        except Exception as exc:  # noqa: BLE001
            log.warning("romanisation failed for a batch: %s", exc)
            continue
        if len(res.words) != len(chunk):
            log.warning("romanisation returned %d of %d words; keeping Devanagari", len(res.words), len(chunk))
            continue
        for k, r in enumerate(res.words):
            r = re.sub(r"^\d+\.\s*", "", r.strip())
            if r:
                out[i + k]["wn"] = out[i + k]["w"]
                out[i + k]["w"] = r
    return out
