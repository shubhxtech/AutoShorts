"""Worker process: runs queued jobs and the periodic scheduler."""

from __future__ import annotations

import signal
import threading
import time

from .. import logs, settings
from ..db import init_db
from ..models import utcnow
from . import queue
from .scheduler import Scheduler

log = logs.get("worker")

LANES = {"heavy": 1, "light": 3}


class Worker:
    def __init__(self, lanes: dict[str, int] | None = None, with_scheduler: bool = True):
        self.lanes = lanes or LANES
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.scheduler = Scheduler(self.stop) if with_scheduler else None

    def _loop(self, lane: str, n: int) -> None:
        while not self.stop.is_set():
            job = queue.claim(lane)
            if job is None:
                self.stop.wait(1.0)
                continue
            queue.run_one(job)

    def _heartbeat(self) -> None:
        while not self.stop.is_set():
            try:
                settings.set("worker_heartbeat", utcnow().isoformat())
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat failed: %s", exc)
            self.stop.wait(10)

    def start(self) -> None:
        from . import tasks  # noqa: F401 - registers every task

        init_db()
        recovered = queue.recover_stale()
        if recovered:
            log.info("re-queued %d jobs left running by a previous worker", recovered)
        for lane, count in self.lanes.items():
            for i in range(count):
                t = threading.Thread(target=self._loop, args=(lane, i), name=f"{lane}-{i}", daemon=True)
                t.start()
                self.threads.append(t)
        hb = threading.Thread(target=self._heartbeat, name="heartbeat", daemon=True)
        hb.start()
        self.threads.append(hb)
        if self.scheduler:
            self.scheduler.start()
        log.info("worker started: %s", ", ".join(f"{k}×{v}" for k, v in self.lanes.items()))

    def run_forever(self) -> None:
        self.start()

        def _stop(*_):
            self.stop.set()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        while not self.stop.is_set():
            time.sleep(0.5)
        log.info("worker stopping")
