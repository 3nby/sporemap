import asyncio
import logging
from datetime import UTC, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from sporemap.core.scans import prune, scan_all, scan_lock
from sporemap.db import get_engine
from sporemap.db.models import Scan, now
from sporemap.settings import get_settings

log = logging.getLogger(__name__)

MAX_NAP = 60  # never sleep longer than this between checks


def _utc(t):
    # sqlite hands datetimes back without a timezone
    if t is None or t.tzinfo is not None:
        return t
    return t.replace(tzinfo=UTC)


class Scheduler:
    """Scans every configured source on its own interval while serve is running.

    scan_every in sporemap.yaml is the default (15m), and any source can set its own
    scan_every, or scan_every: off to only scan by hand. When a source last ran comes
    from the scans table, so a manual scan pushes the next one back and a restart
    doesnt rescan everything at once.
    """

    def __init__(self, sources=None):
        self.srcs = sources  # None = load the installed ones, tests pass their own
        self.on = False  # set by the app, just for status
        self.tried = {}  # name -> last attempt, for sources that never get a Scan row (bad config)
        self.running = []  # names being scanned right now

    def intervals(self):
        # name -> seconds, or None if that source is off
        s = get_settings()
        out = {}
        for n, c in s.sources.items():
            out[n] = c["scan_every"] if isinstance(c, dict) and "scan_every" in c else s.scan_every
        return out

    def last_runs(self):
        with Session(get_engine()) as s:
            rows = s.exec(select(Scan.source, func.max(Scan.started_at)).group_by(Scan.source)).all()
        out = {n: _utc(t) for n, t in rows}
        for n, t in self.tried.items():
            if n not in out or t > out[n]:
                out[n] = t
        return out

    def plan(self, t=None):
        # name -> (every, last, next). next is None when the source is off
        t = t or now()
        last = self.last_runs()
        out = {}
        for n, e in self.intervals().items():
            ln = last.get(n)
            if e is None:
                nx = None
            elif ln is None:
                nx = t  # never scanned, go now
            else:
                nx = ln + timedelta(seconds=e)
            out[n] = (e, ln, nx)
        return out

    def due(self):
        t = now()
        return sorted(n for n, (e, ln, nx) in self.plan(t).items() if nx is not None and nx <= t)

    async def tick(self):
        """Scan whatever is due right now. Returns the names it scanned."""
        if not self.due():
            return []
        async with scan_lock():
            d = self.due()  # again, a manual scan might have run while we waited
            if not d:
                return []
            self.running = d
            res = []
            try:
                res, _ = await scan_all(d, sources=self.srcs)
            except Exception:  # never let one bad run kill the loop
                log.exception("scheduled scan of " + ", ".join(d) + " blew up")
            finally:
                self.running = []
                t = now()
                for n in d:
                    self.tried[n] = t
            try:
                await asyncio.to_thread(prune)  # still holding the lock, so no scan is half written
            except Exception:
                log.exception("pruning old scans failed")
        for x in res:
            if x["error"]:
                log.warning("scheduled scan of " + x["name"] + " failed: " + x["error"])
        # print("ticked", d)
        return d

    def nap(self):
        # seconds until the next source is due, between 1 and MAX_NAP
        nxs = [nx for e, ln, nx in self.plan().values() if nx is not None]
        if not nxs:
            return MAX_NAP
        w = (min(nxs) - now()).total_seconds()
        return min(max(w, 1), MAX_NAP)

    async def run(self):
        log.info("scheduler started")
        while True:
            try:
                await self.tick()
                w = self.nap()
            except Exception:  # db hiccup or similar, try again later
                log.exception("scheduler tick failed")
                w = MAX_NAP
            await asyncio.sleep(w)

    def status(self):
        out = []
        for n, (e, ln, nx) in sorted(self.plan().items()):
            out.append({"name": n, "every": e, "last_run": ln, "next_run": nx})
        return {"enabled": self.on, "running": list(self.running), "sources": out}
