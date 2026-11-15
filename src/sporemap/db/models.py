from datetime import UTC, datetime

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def now():
    return datetime.now(UTC)


class Scan(SQLModel, table=True):
    # one run of one source
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    status: str = "running"  # running / ok / failed
    error: str | None = None
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None


class Node(SQLModel, table=True):
    # the merged thing (host, workload, app...)
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    name: str
    attrs: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    confidence: float = 1.0
    stale: bool = False
    first_seen: datetime = Field(default_factory=now)
    last_seen: datetime = Field(default_factory=now)


class Observation(SQLModel, table=True):
    # what one source saw in one scan, raw-ish
    id: int | None = Field(default=None, primary_key=True)
    scan_id: int = Field(foreign_key="scan.id", index=True)
    node_id: int | None = Field(default=None, foreign_key="node.id", index=True)  # set by the correlator
    source: str
    ext_id: str  # stable id inside that source
    kind: str
    attrs: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    keys: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    seen_at: datetime = Field(default_factory=now)


class Edge(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("kind", "src_id", "dst_id"),)

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # hosts, runs, mounts, exposes, routes_to, member_of
    src_id: int = Field(foreign_key="node.id", index=True)
    dst_id: int = Field(foreign_key="node.id", index=True)
    source: str  # which source said so
    confidence: float = 1.0
    first_seen: datetime = Field(default_factory=now)
    last_seen: datetime = Field(default_factory=now)


class Override(SQLModel, table=True):
    # user edits that always win: rename, icon, hide, pin, merge/split
    __table_args__ = (UniqueConstraint("node_id", "field"),)

    id: int | None = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="node.id", index=True)
    field: str
    value: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)
