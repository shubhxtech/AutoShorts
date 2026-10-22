"""Sentence restoration for unpunctuated transcripts (Whisper often skips punctuation in Hindi and fast speech).

The local LLM reads numbered words and says which ones end a sentence. We store that as a hidden
`eos` flag on the word (the visible text is unchanged), and sentence splitting uses it. Every later
step - scouting, the editor pass, snapping, captions - then works on real sentences.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .. import llm, logs
from .transcribe import SENT_END

log = logs.get("punctuate")


class Ends(BaseModel):
    ends: list[int] = Field(description="numbers of the words that end a sentence or complete thought")


SYSTEM = ("You restore sentence boundaries in a raw speech transcript (Hindi, English or Hinglish). "
          "Mark the word that ends each complete sentence or thought. Typical sentences are 6-25 words.")


def needs_restoring(words: list[dict]) -> bool:
    if len(words) < 60:
        return False
    marks = sum(1 for w in words if SENT_END.search(w["w"]))
    return marks < len(words) / 30          # fewer than one full stop per ~30 words


def restore(words: list[dict], chunk: int = 160, progress=None) -> list[dict]:
    if not llm.available():
        return words
    out = [dict(w) for w in words]
    n = len(words)
    for i in range(0, n, chunk):
        part = words[i:i + chunk]
        listing = " ".join(f"{k + 1}:{w['w']}" for k, w in enumerate(part))
        try:
            res = llm.structured(Ends, SYSTEM, f"Words (number:word):\n{listing}\n\nWhich word numbers end a sentence?",
                                 max_tokens=500, temperature=0.1)
        except Exception as exc:  # noqa: BLE001
            log.warning("sentence restoration failed for words %d-%d: %s", i, i + chunk, exc)
            continue
        for e in res.ends:
            if 1 <= e <= len(part):
                out[i + e - 1]["eos"] = True
        if progress:
            progress(min(1.0, (i + chunk) / n), "restoring sentences")
    log.info("restored %d sentence ends over %d words", sum(1 for w in out if w.get("eos")), n)
    return out
