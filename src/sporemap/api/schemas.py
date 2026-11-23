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


class Attention(BaseModel):
    level: str  # bad / warn / info
    what: str  # host, container, tunnel, source, unknown, mac, ip, workload, dns, route, stale
    text: str
    node_id: int | None  # where to jump on the map, if anywhere


class SourceState(BaseModel):
    name: str
    status: str  # ok / failed / running
    error: str | None
    at: datetime
    last_ok: datetime | None


class HostLoad(BaseModel):
    id: int
    name: str
    cpus: int | None
    mem: int | None  # bytes
    cpus_running: int  # asked for by running CTs/VMs
    mem_running: int
    cpus_all: int  # incl stopped ones
    mem_all: int
    running: int
    total: int


class OverviewOut(BaseModel):
    counts: dict[str, int]
    health: dict[str, list[int]]  # name -> [good, total]
    attention: list[Attention]
    sources: list[SourceState]
    capacity: list[HostLoad]


class OverrideIn(BaseModel):
    value: dict


class OverrideOut(BaseModel):
    node_id: int
    field: str
    value: dict


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


class ScanResult(BaseModel):
    name: str
    scan: ScanOut | None  # None when it never got to scan (bad config, not installed)
    error: str | None
