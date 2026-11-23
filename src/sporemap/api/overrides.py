from fastapi import APIRouter, HTTPException
from sqlmodel import Session, delete, select

from sporemap.api.schemas import OverrideIn, OverrideOut
from sporemap.db import get_engine
from sporemap.db.models import Node, Override, now

r = APIRouter()

# user edits that win over what the sources say. only pins for now, rename/icon/hide later
FIELDS = {"pos"}


def _check(field):
    if field not in FIELDS:
        raise HTTPException(400, "cant override " + field + ", only: " + ", ".join(sorted(FIELDS)))


@r.get("/overrides", response_model=list[OverrideOut])
def list_overrides(field: str | None = None):
    with Session(get_engine()) as s:
        q = select(Override)
        if field:
            q = q.where(Override.field == field)
        rows = s.exec(q.order_by(Override.node_id)).all()
    return [OverrideOut(node_id=o.node_id, field=o.field, value=o.value) for o in rows]


@r.put("/overrides/{nid}/{field}", response_model=OverrideOut)
def set_override(nid: int, field: str, body: OverrideIn):
    """Create or replace one override. For pos the value is {x, y, in}: the spot inside its box."""
    _check(field)
    with Session(get_engine()) as s:
        if s.get(Node, nid) is None:
            raise HTTPException(404, "no node " + str(nid))
        o = s.exec(select(Override).where(Override.node_id == nid, Override.field == field)).first()
        if o is None:
            o = Override(node_id=nid, field=field, value=body.value)
        else:
            o.value = body.value
            o.created_at = now()
        s.add(o)
        s.commit()
        s.refresh(o)
    # print("override", nid, field, body.value)
    return OverrideOut(node_id=o.node_id, field=o.field, value=o.value)


@r.delete("/overrides/{nid}/{field}", status_code=204)
def drop_override(nid: int, field: str):
    _check(field)
    with Session(get_engine()) as s:
        s.exec(delete(Override).where(Override.node_id == nid, Override.field == field))
        s.commit()


@r.delete("/overrides", status_code=204)
def drop_all(field: str):
    """Drop every override of one kind, like field=pos to unpin everything."""
    _check(field)
    with Session(get_engine()) as s:
        s.exec(delete(Override).where(Override.field == field))
        s.commit()
