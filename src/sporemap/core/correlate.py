"""Turn the latest observations from every source into one graph.

First pass, kept simple on purpose:
- merge observations of the SAME kind that share a strong key (mac, vmid, fqdn...)
- weak keys (ip, hostname) only help resolve links, they never merge nodes yet
- node ids stay stable across runs so overrides keep pointing at the right thing
- nodes that vanish get marked stale, never deleted
"""
from collections import Counter

from sqlmodel import Session, func, select

from sporemap.db import get_engine
from sporemap.db.models import Edge, Node, Observation, RawLink, Scan, now

STRONG = ("mac:", "pve:", "docker:", "machine:", "fqdn:", "cftunnel:")


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]  # path halving, ez speedup
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)  # lower index wins so results are deterministic


def _strip_port(k):
    # "ip:192.168.1.21:8080" -> "ip:192.168.1.21", anything else -> None
    if not k.startswith("ip:"):
        return None
    rest = k[3:]
    if rest.count(":") != 1:  # no port, or ipv6, skip for now
        return None
    return "ip:" + rest.split(":")[0]


def correlate():
    t = now()
    with Session(get_engine()) as s:
        # newest successful scan per source
        ids = s.exec(select(func.max(Scan.id)).where(Scan.status == "ok").group_by(Scan.source)).all()
        obs = s.exec(select(Observation).where(Observation.scan_id.in_(ids)).order_by(Observation.id)).all()
        links = s.exec(select(RawLink).where(RawLink.scan_id.in_(ids)).order_by(RawLink.id)).all()

        # 1. merge on strong keys
        uf = _UF(len(obs))
        seen = {}
        for i, o in enumerate(obs):
            for k in o.keys:
                if k.startswith(STRONG):
                    j = seen.setdefault((o.kind, k), i)
                    uf.union(i, j)
        groups = {}
        for i in range(len(obs)):
            groups.setdefault(uf.find(i), []).append(i)

        # 2. reuse node ids from the last run where we can
        prev = {}
        # include the current scans too: a source scanned once already got its node ids
        # last run, and skipping those gave it brand new nodes every time anything else scanned
        for o in s.exec(select(Observation).where(Observation.node_id.is_not(None)).order_by(Observation.id)).all():
            prev[(o.source, o.ext_id)] = o.node_id  # later rows overwrite, so newest wins
        claimed = set()
        gnode = {}
        for root, members in groups.items():
            first = obs[members[0]]
            votes = Counter(prev[(obs[m].source, obs[m].ext_id)] for m in members
                            if (obs[m].source, obs[m].ext_id) in prev)
            nid = next((n for n, _ in votes.most_common() if n not in claimed), None)
            nd = s.get(Node, nid) if nid else None
            if nd is None:
                nd = Node(kind=first.kind, name=first.name, first_seen=t)
            attrs = {}
            for m in members:
                for k, v in obs[m].attrs.items():
                    attrs.setdefault(k, v)  # first source to say it wins, trust tiers come later
            nd.kind, nd.name, nd.attrs = first.kind, first.name or first.ext_id, attrs
            nd.last_seen, nd.stale = t, False
            s.add(nd)
            s.flush()
            claimed.add(nd.id)
            gnode[root] = nd
            for m in members:
                obs[m].node_id = nd.id
                s.add(obs[m])

        # 3. resolve links into edges
        by_ext = {(o.scan_id, o.ext_id): gnode[uf.find(i)] for i, o in enumerate(obs)}
        by_key = {}
        for i, o in enumerate(obs):
            for k in o.keys:
                by_key.setdefault(k, gnode[uf.find(i)])

        holders = {n.attrs.get("placeholder"): n for n in s.exec(select(Node).where(Node.kind == "external")).all()
                   if n.attrs.get("placeholder")}

        def resolve(scan_id, dst):
            if (scan_id, dst) in by_ext:
                return by_ext[(scan_id, dst)]
            if dst in by_key:
                return by_key[dst]
            bare = _strip_port(dst)
            if bare and bare in by_key:
                return by_key[bare]
            # nothing claims it, make a placeholder. thats a drift signal later
            if dst not in holders:
                n = Node(kind="external", name=dst, attrs={"placeholder": dst}, confidence=0.3, first_seen=t)
                s.add(n)
                s.flush()
                holders[dst] = n
            h = holders[dst]
            h.last_seen, h.stale = t, False
            claimed.add(h.id)
            return h

        scan_src = {o.scan_id: o.source for o in obs}
        want = {}
        for ln in links:
            src = resolve(ln.scan_id, ln.src)  # src can be a key too now
            dst = resolve(ln.scan_id, ln.dst)
            if src.id == dst.id:
                continue  # self loops are noise
            want[(ln.kind, src.id, dst.id)] = scan_src[ln.scan_id]

        old = {(e.kind, e.src_id, e.dst_id): e for e in s.exec(select(Edge)).all()}
        for key, source in want.items():
            e = old.pop(key, None) or Edge(kind=key[0], src_id=key[1], dst_id=key[2], source=source, first_seen=t)
            e.last_seen = t
            s.add(e)
        for e in old.values():  # edges nobody reports anymore
            s.delete(e)

        # 4. anything not touched this run is stale
        for n in s.exec(select(Node).where(Node.stale.is_(False))).all():
            if n.id not in claimed:
                n.stale = True
                s.add(n)

        s.commit()
        res = {"nodes": len(claimed), "edges": len(want)}
        # print(res)
        return res
