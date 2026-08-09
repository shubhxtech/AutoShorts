"""Rights gate.

Nothing is downloaded, rendered or published unless it traces back to an
active source with a rights type, proof, and (if set) an unexpired term that
covers the target platform. This is what keeps every connected page safe from
copyright strikes and originality penalties.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import RIGHTS_TYPES, Source, utcnow


class RightsError(Exception):
    pass


@dataclass
class RightsCheck:
    ok: bool
    problems: list[str]

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise RightsError("; ".join(self.problems))


def check_source(src: Source | None, platform: str | None = None) -> RightsCheck:
    problems: list[str] = []
    if src is None:
        return RightsCheck(False, ["video is not linked to a rights record"])
    if not src.active:
        problems.append("rights record is inactive")
    if src.rights_type not in RIGHTS_TYPES:
        problems.append(f"unknown rights type {src.rights_type!r}")
    if src.rights_type != "own" and not (src.proof_url or src.proof_file or src.proof_notes.strip()):
        problems.append("no proof of permission attached (email, contract or campaign link)")
    if src.expires_at is not None and src.expires_at <= utcnow():
        problems.append(f"rights expired on {src.expires_at:%Y-%m-%d}")
    if platform and platform not in (src.allowed_platforms or []):
        problems.append(f"rights don't cover {platform}")
    return RightsCheck(not problems, problems)


def require(src: Source | None, platform: str | None = None) -> Source:
    check_source(src, platform).raise_if_failed()
    assert src is not None
    return src


def badge(src: Source | None) -> dict:
    """Compact rights summary shown on every clip in the review queue."""
    if src is None:
        return {"ok": False, "label": "No rights", "detail": "Not linked to a rights record"}
    chk = check_source(src)
    days_left = None
    if src.expires_at:
        days_left = (src.expires_at - utcnow()).days
    labels = {"own": "Own content", "licensed": "Licensed", "creator_permission": "Creator permission",
              "clipping_campaign": "Clipping campaign"}
    return {
        "ok": chk.ok,
        "label": labels.get(src.rights_type, src.rights_type),
        "detail": "; ".join(chk.problems) if chk.problems else (src.credit_text or src.name),
        "days_left": days_left,
        "platforms": src.allowed_platforms,
    }
