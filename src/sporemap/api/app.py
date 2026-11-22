import asyncio
import contextlib
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sporemap import __version__
from sporemap.api import graph, scans
from sporemap.core.schedule import Scheduler
from sporemap.db import get_engine, migrate
from sporemap.settings import get_settings

r = APIRouter(prefix="/api/v1")

UI = Path(__file__).parent.parent / "ui"  # vite builds web/ into here

NO_UI = """<!doctype html><title>Sporemap</title>
<body style="font-family:sans-serif;max-width:40rem;margin:4rem auto">
<h1>Sporemap</h1>
<p>The API is running, but the web UI hasn't been built.</p>
<p>From the repo: <code>cd web</code>, <code>npm install</code>, <code>npm run build</code>, then reload.</p>
<p>API docs: <a href="/docs">/docs</a></p>
"""


@r.get("/health")
def health():
    db = "ok"
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except SQLAlchemyError:  # dont leak details here
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "version": __version__, "db": db}


@contextlib.asynccontextmanager
async def _life(a):
    sch = a.state.sched
    t = asyncio.create_task(sch.run()) if sch.on else None
    yield
    if t is not None:
        # a scan cut off here stays "running" in the db, next start just scans again
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


def create_app(sources=None):
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)  # db + keys live here
    migrate()  # no-op when already up to date

    a = FastAPI(title="Sporemap", version=__version__, lifespan=_life)
    a.state.sched = Scheduler(sources)
    a.state.sched.on = s.schedule
    a.include_router(r)
    a.include_router(graph.r, prefix="/api/v1", tags=["graph"])
    a.include_router(scans.r, prefix="/api/v1", tags=["scans"])
    _mount_ui(a, UI)
    # print("app built")
    return a


def _mount_ui(a, d):
    # mounted last so /api and /docs win over static files
    if (d / "index.html").exists():
        a.mount("/", StaticFiles(directory=d, html=True), name="ui")
        return

    @a.get("/", response_class=HTMLResponse, include_in_schema=False)
    def no_ui():
        return NO_UI
