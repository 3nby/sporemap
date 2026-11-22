import asyncio

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session, select

from sporemap.api.schemas import ScanIn, ScanOut, ScheduleOut
from sporemap.core.correlate import correlate
from sporemap.core.scans import run_scan, scan_lock
from sporemap.db import get_engine
from sporemap.db.models import Scan
from sporemap.sources import load_sources
from sporemap.sources.config import SourceConfigError, build_config

r = APIRouter()


@r.post("/scans", response_model=ScanOut, status_code=201)
async def start_scan(body: ScanIn):
    """Run a source now, then rebuild the graph. Waits until it finishes."""
    srcs = load_sources()
    if body.source not in srcs:
        raise HTTPException(404, "no source named " + body.source)
    try:
        cfg = build_config(body.source, srcs[body.source])
    except SourceConfigError as e:
        raise HTTPException(400, str(e)) from e
    async with scan_lock():  # waits for a scheduled run to finish first
        sc = await run_scan(body.source, cfg=cfg, sources=srcs)
        if sc.status == "ok":
            await asyncio.to_thread(correlate)  # sync db work, keep it off the event loop
    return ScanOut(**sc.model_dump())


@r.get("/schedule", response_model=ScheduleOut)
def schedule(req: Request):
    """When each source last ran and when it runs next. every is in seconds, null = off."""
    return req.app.state.sched.status()


@r.get("/scans", response_model=list[ScanOut])
def list_scans(limit: int = 50):
    with Session(get_engine()) as s:
        rows = s.exec(select(Scan).order_by(Scan.id.desc()).limit(min(limit, 500))).all()
    return [ScanOut(**x.model_dump()) for x in rows]


@r.get("/scans/{sid}", response_model=ScanOut)
def get_scan(sid: int):
    with Session(get_engine()) as s:
        x = s.get(Scan, sid)
    if x is None:
        raise HTTPException(404, "no scan " + str(sid))
    return ScanOut(**x.model_dump())
