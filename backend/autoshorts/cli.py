"""Command line: `autoshorts up` starts everything."""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__

app = typer.Typer(add_completion=False, help="AutoShorts – automated short-video studio")
console = Console()


def _boot(log_name: str = "autoshorts") -> None:
    from .db import init_db
    from .logs import setup_logging

    setup_logging(log_name)
    init_db()


def _serve(host: str, port: int) -> None:
    import uvicorn

    from .logs import setup_logging

    setup_logging("api")
    uvicorn.run("autoshorts.api.app:app", host=host, port=port, log_level="warning", access_log=False)


def _work() -> None:
    from .jobs.worker import Worker
    from .logs import setup_logging

    setup_logging("worker")
    Worker().run_forever()


@app.command()
def up(port: int = typer.Option(None, help="Dashboard port (default 3000)"),
       host: str = typer.Option(None, help="Bind address (default 127.0.0.1)")):
    """Start the dashboard, the worker and the scheduler."""
    from . import config

    _boot()
    port = port or config.port()
    host = host or config.host()
    os.environ["AUTOSHORTS_PORT"] = str(port)
    ctx = mp.get_context("spawn")
    api = ctx.Process(target=_serve, args=(host, port), name="autoshorts-api", daemon=False)
    worker = ctx.Process(target=_work, name="autoshorts-worker", daemon=False)
    api.start()
    worker.start()
    console.print(f"[bold green]AutoShorts {__version__}[/] running → [link]http://localhost:{port}[/link]")
    console.print(f"data: {config.paths().home}")
    stopping = False

    def stop(*_):
        nonlocal stopping
        if stopping:
            return
        stopping = True
        console.print("stopping…")
        for p in (api, worker):
            if p.is_alive():
                p.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        for p in (api, worker):
            if not p.is_alive() and not stopping:
                console.print(f"[red]{p.name} exited ({p.exitcode}); restarting[/]")
                np_ = ctx.Process(target=_serve if p is api else _work,
                                  args=(host, port) if p is api else (), name=p.name)
                np_.start()
                if p is api:
                    api = np_
                else:
                    worker = np_
        time.sleep(1)
    for p in (api, worker):
        p.join(timeout=10)
        if p.is_alive():
            p.kill()


@app.command()
def serve(port: int = 3000, host: str = "127.0.0.1"):
    """Dashboard/API only."""
    _boot("api")
    _serve(host, port)


@app.command()
def worker():
    """Worker + scheduler only."""
    _boot("worker")
    _work()


@app.command("add-file")
def add_file(path: Path, name: str = typer.Option("", help="Source name"),
             rights: str = typer.Option("own", help="own | licensed | creator_permission | clipping_campaign"),
             credit: str = typer.Option("", help="Credit text shown on the end card"),
             niche: str = typer.Option(""), run: bool = typer.Option(True, help="Process right away in this terminal")):
    """Register a local video you have rights to and (optionally) process it now."""
    from .db import save
    from .jobs.queue import enqueue
    from .models import Source, Video

    _boot()
    path = path.expanduser().resolve()
    if not path.exists():
        raise typer.BadParameter(f"{path} not found")
    src = Source(name=name or path.stem, kind="direct_url", url=str(path), rights_type=rights, credit_text=credit,
                 niche=niche, watch=False, proof_notes="added from the command line by the owner" if rights == "own" else "")
    src = save(src)
    video = save(Video(source_id=src.id, external_id=f"file:{path}", url=str(path), title=path.stem, status="queued"))
    enqueue("process_video", {"video_id": video.id}, dedupe_key=f"process:{video.id}")
    console.print(f"source #{src.id}, video #{video.id} queued")
    if run:
        drain(timeout=7200)


@app.command("add-url")
def add_url(url: str,
            rights: str = typer.Option("own", help="own | licensed | creator_permission | clipping_campaign"),
            i_have_rights: bool = typer.Option(False, "--i-have-rights", help="Confirm you own it or have permission"),
            proof: str = typer.Option("", help="Link to the licence, permission email or campaign"),
            credit: str = typer.Option("", help="Credit text for captions and the end card"),
            niche: str = typer.Option(""), run: bool = typer.Option(True, help="Process right away in this terminal")):
    """Paste a video link (YouTube or any yt-dlp supported site) and turn it into clips."""
    from .api.routes import QuickIn, quick_add
    from .db import session

    _boot()
    if not i_have_rights:
        raise typer.BadParameter("add --i-have-rights to confirm you own this video or have permission to clip it")
    with session() as s:
        res = quick_add(QuickIn(url=url, rights_type=rights, attest=True, proof_url=proof, credit_text=credit,
                                niche=niche), s)
    console.print(f"video #{res['video']['id']} queued: {res['video']['title'] or url}")
    if run:
        drain(timeout=7200)


@app.command()
def drain(timeout: int = typer.Option(7200, help="Give up after this many seconds")):
    """Run the worker in this terminal until the queue is empty."""
    from sqlmodel import select

    from .db import session
    from .jobs.worker import Worker
    from .models import Job

    _boot()
    w = Worker(with_scheduler=False)
    w.start()
    t0 = time.time()
    last = ""
    try:
        while time.time() - t0 < timeout:
            with session() as s:
                active = s.exec(select(Job).where(Job.status.in_(("queued", "running")))).all()
            if not active:
                break
            running = [j for j in active if j.status == "running"]
            line = " | ".join(f"{j.kind}#{j.id} {j.progress * 100:.0f}% {j.message[:60]}" for j in running)
            if line != last:
                console.print(f"[dim]{time.strftime('%H:%M:%S')}[/] {line or f'{len(active)} queued'}")
                last = line
            time.sleep(1)
    finally:
        w.stop.set()
    status()


@app.command()
def status():
    """Pipeline summary."""
    from sqlmodel import select

    from .db import session
    from .models import Clip, Job, Video

    _boot()
    with session() as s:
        videos = s.exec(select(Video).order_by(Video.id.desc()).limit(10)).all()
        clips = s.exec(select(Clip).order_by(Clip.id.desc()).limit(30)).all()
        failed = s.exec(select(Job).where(Job.status == "failed").order_by(Job.id.desc()).limit(5)).all()
    t = Table("video", "status", "title", "note")
    for v in videos:
        t.add_row(str(v.id), v.status, v.title[:50], (v.error or v.stage_message)[:60])
    console.print(t)
    c = Table("clip", "variant", "status", "dur", "style", "hook", "file")
    for cl in clips:
        c.add_row(str(cl.id), cl.variant, cl.status, f"{cl.duration_s:.0f}s", f"{cl.caption_style}/{cl.opening}",
                  cl.hook_text[:40], cl.file_path[-50:] if cl.file_path else cl.error[:50])
    console.print(c)
    for j in failed:
        console.print(f"[red]failed job {j.id} {j.kind}: {j.error[:300]}[/]")


@app.command()
def mcp():
    """Run the MCP server (stdio) so AI assistants like Claude or Cursor can use AutoShorts. Needs `autoshorts up`."""
    try:
        from .mcp_server import run
    except ImportError:
        console.print("[red]Install the MCP extra first:[/] pip install 'autoshorts[mcp]'")
        raise typer.Exit(1) from None
    run()


@app.command()
def version():
    console.print(__version__)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
