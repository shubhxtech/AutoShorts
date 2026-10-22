from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import paths


@lru_cache
def engine():
    db_path = paths().db
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    return eng


def init_db() -> None:
    from . import models  # noqa: F401 - registers tables

    SQLModel.metadata.create_all(engine())
    try:
        os.chmod(paths().db, 0o600)
    except OSError:
        pass


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def save(obj, s: Session | None = None):
    """Add, commit and refresh a single object."""
    if hasattr(obj, "updated_at"):
        from .models import utcnow
        obj.updated_at = utcnow()
    if s is not None:
        s.add(obj)
        s.commit()
        s.refresh(obj)
        return obj
    with session() as s2:
        s2.add(obj)
        s2.commit()
        s2.refresh(obj)
        return obj
