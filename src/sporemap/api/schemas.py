from datetime import datetime

from pydantic import BaseModel


class NodeOut(BaseModel):
    id: int
    kind: str
    name: str
    attrs: dict
    confidence: float
    stale: bool
    first_seen: datetime
    last_seen: datetime


class EdgeOut(BaseModel):
    id: int
    kind: str
    src: int  # node id
    dst: int  # node id
    source: str
    confidence: float


class GraphOut(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


class ScanIn(BaseModel):
    source: str


class ScanOut(BaseModel):
    id: int
    source: str
    status: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None
