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


class SeenBy(BaseModel):
    source: str
    ext_id: str  # what that source calls it, like "lxc/101"
    name: str
    seen_at: datetime


class NodeDetail(NodeOut):
    seen_by: list[SeenBy]  # one per source in its latest good scan


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


class ScheduledSource(BaseModel):
    name: str
    every: float | None  # seconds, None = only by hand
    last_run: datetime | None
    next_run: datetime | None


class ScheduleOut(BaseModel):
    enabled: bool
    running: list[str]
    sources: list[ScheduledSource]


class ScanOut(BaseModel):
    id: int
    source: str
    status: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None
