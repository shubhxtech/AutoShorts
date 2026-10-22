import os
import tempfile

os.environ["AUTOSHORTS_HOME"] = tempfile.mkdtemp(prefix="cf_test_")

from autoshorts.db import init_db  # noqa: E402

init_db()


def make_words(text: str, start: float = 0.0, gap: float = 0.05, dur: float = 0.25, pauses: dict | None = None):
    """Word list with optional extra pauses after given word indexes."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        out.append({"w": w, "s": round(t, 3), "e": round(t + dur, 3), "p": 0.95})
        t += dur + gap + (pauses or {}).get(i, 0)
    return out
