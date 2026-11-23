from fastapi import APIRouter, HTTPException
from sqlmodel import Session, col, func, select

from sporemap.api.schemas import (
    EdgeOut,
    GraphOut,
    NodeDetail,
    NodeOut,
    OverviewOut,
    SeenBy,
)
from sporemap.core.overview import overview as _overview
from sporemap.db import get_engine
from sporemap.db.models import Edge, Node, Observation, Scan

r = APIRouter()


@r.get("/graph", response_model=GraphOut)
def graph(stale: bool = False, kind: str | None = None):
    """The whole graph. Stale nodes are hidden unless stale=true."""
    with Session(get_engine()) as s:
        q = select(Node)
        if not stale:
            q = q.where(Node.stale.is_(False))
        if kind:
            q = q.where(Node.kind == kind)
        nodes = s.exec(q.order_by(Node.id)).all()
        ids = {n.id for n in nodes}
        # only edges where both ends made it through the filter
        edges = [e for e in s.exec(select(Edge).order_by(Edge.id)).all() if e.src_id in ids and e.dst_id in ids]
    return GraphOut(
        nodes=[NodeOut(**n.model_dump()) for n in nodes],
        edges=[EdgeOut(id=e.id, kind=e.kind, src=e.src_id, dst=e.dst_id, source=e.source,
                       confidence=e.confidence) for e in edges],
    )


@r.get("/overview", response_model=OverviewOut)
def overview():
    """How the lab is doing: health counts, what needs a look, sources, and host capacity."""
    return _overview()


@r.get("/nodes/{nid}", response_model=NodeDetail)
def node(nid: int):
    """One node plus which sources saw it in their latest good scan (for the inspector)."""
    with Session(get_engine()) as s:
        n = s.get(Node, nid)
        if n is None:
            raise HTTPException(404, "no node " + str(nid))
        # same scans the correlator builds from, so this matches the graph
        latest = s.exec(select(func.max(Scan.id)).where(Scan.status == "ok").group_by(Scan.source)).all()
        obs = s.exec(select(Observation).where(Observation.node_id == nid, col(Observation.scan_id).in_(latest))
                     .order_by(Observation.source)).all()
    seen = [SeenBy(source=o.source, ext_id=o.ext_id, name=o.name, seen_at=o.seen_at) for o in obs]
    # print(nid, [o.source for o in obs])
    return NodeDetail(**n.model_dump(), seen_by=seen)
