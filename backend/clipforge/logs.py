from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import paths

_configured = False


def setup_logging(name: str = "clipforge", level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    fh = RotatingFileHandler(paths().logs / f"{name}.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    for noisy in ("httpx", "httpx2", "googleapiclient.discovery_cache", "urllib3", "PIL", "numba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"clipforge.{name}")
