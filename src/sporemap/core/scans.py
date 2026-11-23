import asyncio
import logging
import weakref

from sqlmodel import Session

from sporemap.db import get_engine
from sporemap.db.models import Observation, RawLink, Scan, now
from sporemap.settings import get_settings
from sporemap.sources import load_sources
from sporemap.sources.config import build_config

log = logging.getLogger(__name__)

_locks = weakref.WeakKeyDictionary()


def scan_lock():
    """The lock the api and the scheduler hold while scanning, so they never overlap.

    One per event loop, bc an asyncio.Lock sticks to the first loop that waits on it
    and tests spin up a fresh loop every time.
    """
    lp = asyncio.get_running_loop()
    if lp not in _locks:
        _locks[lp] = asyncio.Lock()
    return _locks[lp]


async def run_scan(name, cfg=None, sources=None):
    """Run one source once and store what it saw. Returns the finished Scan row.

    A source blowing up never raises out of here, it just marks the scan failed.
    Raises KeyError for an unknown source and SourceConfigError for bad config,
    bc those are setup problems, not scan problems.
    """
    srcs = sources if sources is not None else load_sources()
    cls = srcs[name]
    src = cls()
    c = cfg if cfg is not None else build_config(name, cls)

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


async def scan_all(names=None, sources=None):
    """Scan several sources one after another, then rebuild the graph once.

    names=None means every source in sporemap.yaml. One source failing (bad config,
    not installed, api down) never stops the rest. Returns (results, graph) where
    results is a list of {"name", "scan", "error"} and graph is correlate()'s counts,
    or None if nothing scanned ok.
    """
    from sporemap.core.correlate import correlate
    from sporemap.sources.config import SourceConfigError

    srcs = sources if sources is not None else load_sources()
    if names is None:
        names = sorted(get_settings().sources)
    out = []
    for n in names:
        if n not in srcs:
            out.append({"name": n, "scan": None, "error": "not installed"})
            continue
        try:
            c = build_config(n, srcs[n])
        except SourceConfigError as e:
            out.append({"name": n, "scan": None, "error": str(e)})
            continue
        sc = await run_scan(n, cfg=c, sources=srcs)
        out.append({"name": n, "scan": sc, "error": sc.error})
    g = None
    if any(x["scan"] is not None and x["scan"].status == "ok" for x in out):
        g = await asyncio.to_thread(correlate)  # sync db work
    # print(out)
    return out, g


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


def forget(name):
    """Drop everything one source ever reported, then rebuild the graph.

    Nodes only that source knew about get deleted (not just marked stale), along with
    their overrides. Returns counts for the cli.
    """
    from sqlmodel import col, delete, select

    from sporemap.core.correlate import correlate
    from sporemap.db.models import Edge, Node, Override

    with Session(get_engine()) as s:
        sids = s.exec(select(Scan.id).where(Scan.source == name)).all()
        if not sids:
            return None
        n_obs = len(s.exec(select(Observation.id).where(col(Observation.scan_id).in_(sids))).all())
        s.exec(delete(RawLink).where(col(RawLink.scan_id).in_(sids)))
        s.exec(delete(Observation).where(col(Observation.scan_id).in_(sids)))
        s.exec(delete(Scan).where(col(Scan.id).in_(sids)))
        s.commit()

    g = correlate()

    with Session(get_engine()) as s:
        seen = set(s.exec(select(Observation.node_id).where(col(Observation.node_id).is_not(None))).all())
        gone = []
        for n in s.exec(select(Node)).all():
            if n.id in seen:
                continue
            if n.attrs.get("placeholder") and not n.stale:
                continue  # some other source still links to it
            gone.append(n.id)
        if gone:
            # correlate already dropped edges nobody reports, this is just belt and suspenders
            s.exec(delete(Edge).where(col(Edge.src_id).in_(gone) | col(Edge.dst_id).in_(gone)))
            s.exec(delete(Override).where(col(Override.node_id).in_(gone)))
            s.exec(delete(Node).where(col(Node.id).in_(gone)))
            s.commit()
    # print("forgot", name, sids, gone)
    return {"scans": len(sids), "observations": n_obs, "removed_nodes": len(gone),
            "nodes": g["nodes"], "edges": g["edges"]}


def prune(keep=None):
    """Delete old scans so the db doesnt grow forever. Returns counts.

    Per source it keeps the newest keep scans (keep_scans in sporemap.yaml) plus the
    latest ok one, which is what the correlator and the node api read, even if a pile
    of failed scans came after it.

    Older scans lose their raw links and observations, except the newest sighting of
    each thing (source + ext_id). The correlator uses those to hand a node its old id
    back when something comes back after being gone a while, so overrides keep working.
    A scan only gets deleted once nothing of it is left.
    """
    from sqlmodel import col, delete, func, select

    n = get_settings().keep_scans if keep is None else keep
    out = {"scans": 0, "observations": 0, "links": 0}
    if not n:
        return out  # keep everything
    with Session(get_engine()) as s:
        keep_ids = set()
        for src in s.exec(select(Scan.source).distinct()).all():
            keep_ids.update(s.exec(select(Scan.id).where(Scan.source == src).order_by(Scan.id.desc()).limit(n)).all())
        keep_ids.update(s.exec(select(func.max(Scan.id)).where(Scan.status == "ok").group_by(Scan.source)).all())
        keep_ids = sorted(keep_ids)
        # newest row of each thing that got a node, sqlite runs this once up front
        last = (select(func.max(Observation.id)).where(col(Observation.node_id).is_not(None))
                .group_by(Observation.source, Observation.ext_id))
        out["links"] = s.exec(delete(RawLink).where(col(RawLink.scan_id).not_in(keep_ids))).rowcount
        out["observations"] = s.exec(delete(Observation).where(col(Observation.scan_id).not_in(keep_ids),
                                                               col(Observation.id).not_in(last))).rowcount
        used = select(Observation.scan_id).distinct()
        out["scans"] = s.exec(delete(Scan).where(col(Scan.id).not_in(keep_ids), col(Scan.id).not_in(used))).rowcount
        s.commit()
    # print("pruned", out)
    return out


def vacuum():
    """Give the space from deleted rows back to the os.

    Not needed after every prune: sqlite reuses freed pages for new rows, so with
    retention on the file just stops growing. This is for shrinking it once, eg after
    the first prune of a db that kept everything. Rewrites the whole file, so its slow-ish
    and wants the db to itself for a moment.
    """
    from sqlalchemy import text

    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text("VACUUM"))
        c.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))  # in wal mode the file only shrinks at a checkpoint
