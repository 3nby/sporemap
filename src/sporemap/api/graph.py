from fastapi import APIRouter
from sqlmodel import Session, select

from sporemap.api.schemas import EdgeOut, GraphOut, NodeOut
from sporemap.db import get_engine
from sporemap.db.models import Edge, Node

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
