import asyncio
import logging

from sqlmodel import Session

from sporemap.db import get_engine
from sporemap.db.models import Observation, RawLink, Scan, now
from sporemap.settings import get_settings
from sporemap.sources import load_sources

log = logging.getLogger(__name__)


async def run_scan(name, cfg=None, sources=None):
    """Run one source once and store what it saw. Returns the finished Scan row.

    A source blowing up never raises out of here, it just marks the scan failed.
    Only an unknown source name raises (KeyError), bc thats a caller bug.
    """
    srcs = sources if sources is not None else load_sources()
    cls = srcs[name]
    src = cls()
    c = cls.Config(**(cfg or {}))

    eng = get_engine()
    with Session(eng) as s:
        sc = Scan(source=name)
        s.add(sc)
        s.commit()
        s.refresh(sc)
        sid = sc.id

    try:
        res = await asyncio.wait_for(src.discover(c), timeout=get_settings().scan_timeout)
        res.check()
    except TimeoutError:
        return _finish(sid, "failed", "timed out after " + str(get_settings().scan_timeout) + "s")
    except Exception as e:  # noqa: BLE001  plugin code can throw anything
        # print(repr(e))
        log.warning("scan of " + name + " failed: " + repr(e))
        return _finish(sid, "failed", type(e).__name__ + ": " + str(e))

    with Session(eng) as s:
        for o in res.obs:
            s.add(Observation(scan_id=sid, source=name, ext_id=o.ext_id, kind=o.kind,
                              name=o.name, attrs=o.attrs, keys=o.keys))
        for ln in res.links:
            s.add(RawLink(scan_id=sid, kind=ln.kind, src=ln.src, dst=ln.dst))
        s.commit()

    for w in res.warnings:
        log.info(name + ": " + w)
    return _finish(sid, "ok", None)


def _finish(sid, status, err):
    with Session(get_engine()) as s:
        sc = s.get(Scan, sid)
        sc.status = status
        sc.error = err
        sc.finished_at = now()
        s.add(sc)
        s.commit()
        s.refresh(sc)
        return sc
