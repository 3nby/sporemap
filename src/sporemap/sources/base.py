from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from pydantic import BaseModel

KINDS = {"host", "workload", "container", "app", "storage", "network", "endpoint", "external"}
EDGES = {"hosts", "runs", "mounts", "exposes", "routes_to", "member_of"}


@dataclass
class Obs:
    kind: str  # one of KINDS
    ext_id: str  # stable id inside this source, like "lxc/202"
    name: str
    attrs: dict = field(default_factory=dict)  # ips, macs, ports, whatever
    keys: list[str] = field(default_factory=list)  # correlation keys like "ip:192.168.1.71"


@dataclass
class Link:
    kind: str  # one of EDGES
    src: str  # ext_id, OR a key (like "ip:10.0.0.5" for "whatever has this ip runs me")
    dst: str  # ext_id, OR a correlation key the correlator resolves later


@dataclass
class Result:
    obs: list[Obs] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def check(self):
        # catch plugin bugs early instead of in the correlator
        ids = set()
        for o in self.obs:
            if o.kind not in KINDS:
                raise ValueError("bad node kind: " + o.kind)
            if o.ext_id in ids:
                raise ValueError("duplicate ext_id: " + o.ext_id)
            ids.add(o.ext_id)
        for ln in self.links:
            if ln.kind not in EDGES:
                raise ValueError("bad edge kind: " + ln.kind)
            # either end can be a key, so only complain when it looks like neither
            if ln.src not in ids and ":" not in ln.src:
                raise ValueError("link from unknown ext_id: " + ln.src)
            if ln.dst not in ids and ":" not in ln.dst:
                raise ValueError("link to unknown ext_id: " + ln.dst)
            if ln.src not in ids and ln.dst not in ids:
                raise ValueError("link needs at least one end in this result: " + ln.src + " -> " + ln.dst)
        return self


class Source(ABC):
    """Base class every discovery source extends.

    Register it under the "sporemap.sources" entry-point group, e.g.
        [project.entry-points."sporemap.sources"]
        mything = "my_pkg.source:MyThing"
    """

    name: ClassVar[str]
    Config: ClassVar[type[BaseModel]]  # pydantic model for creds + settings

    @abstractmethod
    async def test(self, cfg) -> None:
        """Raise if the config cant reach the system. Return None if ok."""

    @abstractmethod
    async def discover(self, cfg) -> Result:
        """Do one full read-only pass and return what you saw."""
