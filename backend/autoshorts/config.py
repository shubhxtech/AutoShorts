"""Paths and static configuration.

Secrets and user-editable settings live in the database (see `settings.py`);
this module only covers what must be known before the database opens.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _default_home() -> Path:
    if sys.platform == "darwin":
        base, new, old = Path.home() / "Library" / "Application Support", "AutoShorts", "ClipForge"
    elif sys.platform == "win32":
        base, new, old = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"), "AutoShorts", "ClipForge"
    else:
        base, new, old = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"), "autoshorts", "clipforge"
    # Installs from before the rename keep their data where it is. (The new folder may already exist
    # because the macOS installer puts its runtime there, so look for an actual database.)
    if not (base / new / "autoshorts.db").exists() and (base / old / "clipforge.db").exists():
        return base / old
    return base / new


class Paths:
    def __init__(self, home: Path):
        self.home = home
        legacy = home / "clipforge.db"          # databases created before the rename keep their name
        self.db = legacy if legacy.exists() and not (home / "autoshorts.db").exists() else home / "autoshorts.db"
        self.library = home / "library"          # original source videos
        self.work = home / "work"                # transcripts, timelines, analysis
        self.clips = home / "clips"              # rendered clips + thumbs
        self.proofs = home / "proofs"            # rights documents
        self.music = home / "music"              # licensed background music
        self.fonts = home / "fonts"              # extra caption fonts (.ttf/.otf)
        self.tmp = home / "tmp"
        self.logs = home / "logs"
        self.secrets = home / "secrets"          # OAuth client files and tokens

    def ensure(self) -> Paths:
        for p in (self.home, self.library, self.work, self.clips, self.proofs, self.music,
                  self.fonts, self.tmp, self.logs, self.secrets):
            p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.secrets, 0o700)
        except OSError:
            pass
        return self


@lru_cache
def paths() -> Paths:
    home = Path(os.environ.get("AUTOSHORTS_HOME") or _default_home()).expanduser()
    return Paths(home).ensure()


def port() -> int:
    return int(os.environ.get("AUTOSHORTS_PORT", "3000"))


def host() -> str:
    return os.environ.get("AUTOSHORTS_HOST", "127.0.0.1")


def base_url() -> str:
    """Address the dashboard and OAuth callbacks are served from."""
    return os.environ.get("AUTOSHORTS_BASE_URL", f"http://localhost:{port()}")


def frontend_dist() -> Path | None:
    env = os.environ.get("AUTOSHORTS_FRONTEND")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [
        here.parent / "web",                                  # packaged inside the app bundle
        here.parents[2] / "frontend" / "dist",                # repo checkout
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None
