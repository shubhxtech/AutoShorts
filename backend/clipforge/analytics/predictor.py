"""Learned performance model.

Predicts a clip's 72h views relative to its page's median from clip features
(LLM sub-scores, audio signals, hook style, caption style, length, opening,
posting hour). Plain ridge regression in numpy: small, fast, explainable, and
good enough with a few dozen published clips. Until there's enough data it
stays out of the way and the LLM's ratings drive ranking.
"""

from __future__ import annotations

import math
from statistics import median

import numpy as np
from sqlmodel import select

from .. import settings
from ..db import session
from ..models import Clip, Moment, Post
from .bandit import DIMENSIONS

SCORE_KEYS = ["hook", "standalone", "emotion", "surprise", "value", "payoff", "quotable"]
SIGNAL_KEYS = ["hook_signal", "peak", "mean_intensity", "laughs", "duration", "rate", "speakers"]
MIN_SAMPLES = 12


def moment_features(m: Moment) -> dict[str, float]:
    f = {f"s_{k}": float((m.scores or {}).get(k, 5)) for k in SCORE_KEYS}
    feats = m.features or {}
    for k in SIGNAL_KEYS:
        v = feats.get(k, 0)
        f[f"x_{k}"] = float(v if not isinstance(v, bool) else int(v))
    f["x_duration"] = f["x_duration"] / 60.0
    return f


def clip_features(c: Clip, m: Moment, slot_hour: int | None = None, platform: str = "") -> dict[str, float]:
    f = moment_features(m)
    for dim, arms in DIMENSIONS.items():
        val = {"hook_style": c.hook_style, "caption_style": c.caption_style, "length_bucket": c.length_bucket,
               "opening": c.opening, "edit_preset": c.edit_preset}.get(dim, "")
        for a in arms:
            f[f"{dim}={a}"] = 1.0 if val == a else 0.0
    if slot_hour is not None and slot_hour >= 0:
        f["hour_sin"] = math.sin(2 * math.pi * slot_hour / 24)
        f["hour_cos"] = math.cos(2 * math.pi * slot_hour / 24)
    f["platform=instagram"] = 1.0 if platform == "instagram" else 0.0
    return f


def _ridge(X: np.ndarray, y: np.ndarray, lam: float = 3.0) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-6
    Z = (X - mu) / sd
    b = float(y.mean())
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - b))
    return w, b, mu, sd


def training_rows() -> list[tuple[Post, Clip, Moment]]:
    with session() as s:
        posts = s.exec(select(Post).where(Post.status == "published", Post.views_72h != None)).all()  # noqa: E711
        out = []
        for p in posts:
            c = s.get(Clip, p.clip_id)
            m = s.get(Moment, c.moment_id) if c else None
            if c and m:
                out.append((p, c, m))
    return out


def relative_targets(rows) -> np.ndarray:
    by_acc: dict[int, list[int]] = {}
    for p, _, _ in rows:
        by_acc.setdefault(p.account_id, []).append(p.views_72h or 0)
    med = {a: max(1.0, median(v)) for a, v in by_acc.items()}
    return np.array([math.log1p(p.views_72h or 0) - math.log1p(med[p.account_id]) for p, _, _ in rows])


def train() -> dict:
    rows = training_rows()
    if len(rows) < MIN_SAMPLES:
        return {"trained": False, "samples": len(rows), "needed": MIN_SAMPLES}
    y = relative_targets(rows)
    clip_dicts = [clip_features(c, m, p.slot_hour, p.platform) for p, c, m in rows]
    keys = sorted({k for d in clip_dicts for k in d})
    X = np.array([[d.get(k, 0.0) for k in keys] for d in clip_dicts])
    w, b, mu, sd = _ridge(X, y)
    resid = y - ((X - mu) / sd @ w + b)
    r2 = 1 - float((resid ** 2).sum() / max(1e-9, ((y - y.mean()) ** 2).sum()))

    mkeys = sorted(moment_features(rows[0][2]).keys())
    Xm = np.array([[moment_features(m).get(k, 0.0) for k in mkeys] for _, _, m in rows])
    wm, bm, mum, sdm = _ridge(Xm, y)

    # Re-weight the LLM sub-scores toward what actually performs (blended with the prior).
    from ..mining.miner import DEFAULT_WEIGHTS

    coef = {k: float(wm[mkeys.index(f"s_{k}")]) for k in SCORE_KEYS}
    pos = {k: max(0.0, v) for k, v in coef.items()}
    total = sum(pos.values())
    if total > 0:
        learned = {k: round(0.5 * DEFAULT_WEIGHTS[k] + 0.5 * pos[k] / total, 4) for k in SCORE_KEYS}
        settings.set("score_weights", learned)

    model = {"keys": keys, "w": w.tolist(), "b": b, "mu": mu.tolist(), "sd": sd.tolist(),
             "mkeys": mkeys, "wm": wm.tolist(), "bm": bm, "mum": mum.tolist(), "sdm": sdm.tolist(),
             "samples": len(rows), "r2": round(r2, 3),
             "top_features": sorted(({"feature": k, "weight": round(float(v), 3)} for k, v in zip(keys, w)),
                                    key=lambda x: -abs(x["weight"]))[:12]}
    settings.set("predictor", model)
    return {"trained": True, "samples": len(rows), "r2": model["r2"], "top_features": model["top_features"]}


def _model() -> dict | None:
    m = settings.get("predictor")
    return m if isinstance(m, dict) and m.get("w") else None


def predict_moment(m: Moment) -> float:
    """0–10 predicted quality; falls back to the miner's score until the model has data."""
    model = _model()
    if not model:
        return round(m.final_score or m.llm_score or 0, 2)
    f = moment_features(m)
    x = np.array([f.get(k, 0.0) for k in model["mkeys"]])
    z = (x - np.array(model["mum"])) / np.array(model["sdm"])
    rel = float(z @ np.array(model["wm"]) + model["bm"])
    return round(max(0.0, min(10.0, 5 + 2.5 * rel)), 2)


def predict_clip(c: Clip, m: Moment, account_median_views: float = 0, slot_hour: int | None = None,
                 platform: str = "") -> tuple[float, float]:
    """(0–10 score, expected 72h views)."""
    model = _model()
    if not model:
        base = m.final_score or m.llm_score or 5
        return round(base, 2), round(account_median_views * (0.6 + base / 10), 0) if account_median_views else 0
    f = clip_features(c, m, slot_hour, platform)
    x = np.array([f.get(k, 0.0) for k in model["keys"]])
    z = (x - np.array(model["mu"])) / np.array(model["sd"])
    rel = float(z @ np.array(model["w"]) + model["b"])
    views = account_median_views * math.exp(rel) if account_median_views else 0
    return round(max(0.0, min(10.0, 5 + 2.5 * rel)), 2), round(views, 0)
