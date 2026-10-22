"""Multi-armed bandit (Thompson sampling) over editing and scheduling choices.

Each arm keeps a Beta(alpha, beta) belief about "does this choice beat the
page's median 72h views?". Sampling from those beliefs balances trying new
options against reusing what already works.
"""

from __future__ import annotations

import random

from sqlmodel import select

from ..db import session
from ..models import BanditArm, utcnow

DIMENSIONS = {
    "hook_style": ["question", "bold_claim", "curiosity_gap", "story", "stat", "controversy", "warning", "relatable"],
    "caption_style": ["clean", "chip", "bold", "karaoke", "pop", "boxed", "minimal"],
    "length_bucket": ["full", "tight"],
    "opening": ["straight", "cold_open"],
    "edit_preset": ["clean", "hype", "minimal", "explainer"],
}

PRIORS = {  # mild starting preferences from what generally works on Shorts/Reels
    ("caption_style", "clean"): (2.5, 1.0),
    ("caption_style", "bold"): (1.5, 1.0),
    ("edit_preset", "clean"): (2.5, 1.0),
    ("caption_style", "pop"): (1.5, 1.0),
    ("opening", "cold_open"): (1.5, 1.0),
    ("hook_style", "curiosity_gap"): (1.5, 1.0),
    ("hook_style", "bold_claim"): (1.5, 1.0),
}


def _arms(scope: str, dimension: str) -> dict[str, BanditArm]:
    with session() as s:
        rows = s.exec(select(BanditArm).where(BanditArm.scope == scope, BanditArm.dimension == dimension)).all()
    return {r.arm: r for r in rows}


def posterior(scope: str, dimension: str, arms: list[str]) -> dict[str, tuple[float, float]]:
    stored = _arms(scope, dimension)
    glob = _arms("global", dimension) if scope != "global" else {}
    out = {}
    for a in arms:
        if a in stored and stored[a].pulls >= 3:
            out[a] = (stored[a].alpha, stored[a].beta)
        elif a in glob:
            out[a] = (glob[a].alpha, glob[a].beta)
        else:
            out[a] = PRIORS.get((dimension, a), (1.0, 1.0))
    return out


def sample(dimension: str, arms: list[str] | None = None, scope: str = "global", rng: random.Random | None = None) -> str:
    arms = arms or DIMENSIONS[dimension]
    rng = rng or random.Random()
    post = posterior(scope, dimension, arms)
    return max(arms, key=lambda a: rng.betavariate(*post[a]))


def best(dimension: str, arms: list[str] | None = None, scope: str = "global") -> str:
    arms = arms or DIMENSIONS[dimension]
    post = posterior(scope, dimension, arms)
    return max(arms, key=lambda a: post[a][0] / (post[a][0] + post[a][1]))


def update(dimension: str, arm: str, success: bool, scope: str = "global", weight: float = 1.0) -> None:
    for sc in {scope, "global"}:
        with session() as s:
            row = s.exec(select(BanditArm).where(BanditArm.scope == sc, BanditArm.dimension == dimension,
                                                 BanditArm.arm == arm)).first()
            if row is None:
                a0, b0 = PRIORS.get((dimension, arm), (1.0, 1.0))
                row = BanditArm(scope=sc, dimension=dimension, arm=arm, alpha=a0, beta=b0)
            if success:
                row.alpha += weight
            else:
                row.beta += weight
            row.pulls += 1
            row.updated_at = utcnow()
            s.add(row)
            s.commit()


def table(scope: str = "global") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with session() as s:
        rows = s.exec(select(BanditArm).where(BanditArm.scope == scope)).all()
    by_dim: dict[str, dict[str, BanditArm]] = {}
    for r in rows:
        by_dim.setdefault(r.dimension, {})[r.arm] = r
    dims = dict(DIMENSIONS)
    for d in by_dim:
        dims.setdefault(d, sorted(by_dim[d]))
    for dim, arms in dims.items():
        lst = []
        for a in arms:
            r = by_dim.get(dim, {}).get(a)
            al, be = (r.alpha, r.beta) if r else PRIORS.get((dim, a), (1.0, 1.0))
            lst.append({"arm": a, "win_rate": round(al / (al + be), 3), "pulls": r.pulls if r else 0,
                        "alpha": al, "beta": be})
        out[dim] = sorted(lst, key=lambda x: -x["win_rate"])
    return out
