"""The overview page: how the lab is doing, what needs a look, and how full each host is."""
from collections import Counter, defaultdict

from sqlmodel import Session, func, select

from sporemap.db import get_engine
from sporemap.db.models import Edge, Node, Scan

# how bad each attention item is, worst first
LEVELS = ("bad", "warn", "info")


def _item(level, what, text, node=None):
    return {"level": level, "what": what, "text": text, "node_id": node}


def _bad_container(a):
    st = str(a.get("status") or "").lower()
    return a.get("state") not in (None, "running") or "unhealthy" in st or "restarting" in st


def overview():
    with Session(get_engine()) as s:
        nodes = s.exec(select(Node).where(Node.stale.is_(False))).all()
        n_stale = s.exec(select(func.count()).select_from(Node).where(Node.stale.is_(True))).one()
        edges = s.exec(select(Edge)).all()
        # newest scan of each source, good or bad, and the newest good one
        last = {}
        for sc in s.exec(select(Scan).order_by(Scan.id)).all():
            last[sc.source] = sc
        last_ok = dict(s.exec(select(Scan.source, func.max(Scan.finished_at)).where(Scan.status == "ok")
                              .group_by(Scan.source)).all())

    by = {n.id: n for n in nodes}
    out_kind = defaultdict(set)  # node id -> edge kinds going out
    host_of = {}
    for e in edges:
        out_kind[e.src_id].add(e.kind)
        if e.kind == "hosts" and e.src_id in by and by[e.src_id].kind == "host":
            host_of[e.dst_id] = e.src_id

    att = []
    work = [n for n in nodes if n.kind == "workload"]
    ctrs = [n for n in nodes if n.kind == "container"]
    tuns = [n for n in nodes if n.kind == "external" and n.attrs.get("type") == "tunnel"]
    hosts = [n for n in nodes if n.kind == "host"]

    # --- things that are down
    for n in hosts:
        if n.attrs.get("status") not in (None, "online", "running"):
            att.append(_item("bad", "host", n.name + " is " + str(n.attrs.get("status")), n.id))
    for n in ctrs:
        if _bad_container(n.attrs):
            att.append(_item("bad", "container", n.name + ": " + str(n.attrs.get("status") or n.attrs.get("state")), n.id))
    for n in tuns:
        st = n.attrs.get("status")
        if st not in (None, "healthy"):
            # a dead tunnel nothing routes through is old clutter, not an outage
            lvl = "bad" if n.attrs.get("routes") else "info"
            att.append(_item(lvl, "tunnel", "tunnel " + n.name + " is " + str(st), n.id))
    for src, sc in sorted(last.items()):
        if sc.status == "failed":
            att.append(_item("bad", "source", src + " failed its last scan: " + str(sc.error), None))

    # --- drift: things that dont add up
    for n in nodes:
        if n.kind == "external" and n.attrs.get("placeholder"):
            att.append(_item("warn", "unknown", "something points at " + n.name + " but no source knows what it is", n.id))
    macs = defaultdict(list)
    ips = defaultdict(list)
    for n in work:
        for nic in n.attrs.get("nics") or []:
            if nic.get("mac"):
                macs[nic["mac"].lower()].append(n)
        for ip in n.attrs.get("ips") or []:
            ips[ip].append(n)
    for n in hosts:
        if n.attrs.get("ip"):
            ips[n.attrs["ip"]].append(n)
    for mac, ns in sorted(macs.items()):
        if len({x.id for x in ns}) > 1:
            att.append(_item("warn", "mac", "MAC " + mac + " is on " + " and ".join(x.name for x in ns)
                             + ", they clash if both run", ns[0].id))
    for ip, ns in sorted(ips.items()):
        if len({x.id for x in ns}) > 1:
            att.append(_item("warn", "ip", "IP " + ip + " is claimed by " + " and ".join(x.name for x in ns), ns[0].id))

    # --- worth knowing, maybe on purpose
    for n in work:
        if n.attrs.get("status") not in (None, "running"):
            att.append(_item("info", "workload", n.name + " is " + str(n.attrs.get("status")), n.id))
    # cloudflared makes endpoint cards for its own hostnames too, so a real DNS record is one
    # with a record type (from the cloudflare source). no cloudflare source = cant tell, skip it
    names = {n.name for n in nodes if n.kind == "endpoint" and n.attrs.get("type")}
    for n in nodes:
        if n.kind == "endpoint" and "routes_to" not in out_kind[n.id] and n.attrs.get("type") in ("A", "AAAA", "CNAME"):
            att.append(_item("info", "dns", n.name + " (" + str(n.attrs.get("type")) + ") doesnt lead to anything sporemap knows", n.id))
    for n in tuns if names else []:
        for h in sorted((n.attrs.get("routes") or {}).keys()):
            if h not in names and not h.startswith("*"):
                att.append(_item("info", "route", "tunnel " + n.name + " routes " + h + " but theres no DNS record for it", n.id))
    if n_stale:
        att.append(_item("info", "stale", str(n_stale) + " things werent seen in the latest scans (show stale on the map)", None))

    att.sort(key=lambda x: LEVELS.index(x["level"]))

    # --- capacity: what the running CTs/VMs ask for vs what the host has
    cap = []
    for h in sorted(hosts, key=lambda x: x.name):
        mine = [w for w in work if host_of.get(w.id) == h.id]
        on = [w for w in mine if w.attrs.get("status") == "running"]
        cap.append({
            "id": h.id, "name": h.name,
            "cpus": h.attrs.get("cpus"), "mem": h.attrs.get("mem"),
            "cpus_running": sum(w.attrs.get("cpus") or 0 for w in on),
            "mem_running": sum(w.attrs.get("mem") or 0 for w in on),
            "cpus_all": sum(w.attrs.get("cpus") or 0 for w in mine),
            "mem_all": sum(w.attrs.get("mem") or 0 for w in mine),
            "running": len(on), "total": len(mine),
        })

    sources = [{"name": src, "status": sc.status, "error": sc.error, "at": sc.finished_at or sc.started_at,
                "last_ok": last_ok.get(src)} for src, sc in sorted(last.items())]

    health = {
        "workloads": [sum(1 for w in work if w.attrs.get("status") == "running"), len(work)],
        "containers": [sum(1 for c in ctrs if not _bad_container(c.attrs)), len(ctrs)],
        "tunnels": [sum(1 for t in tuns if t.attrs.get("status") == "healthy"), len(tuns)],
        "sources": [sum(1 for x in sources if x["status"] == "ok"), len(sources)],
    }
    # print(len(att), "attention items")
    return {"counts": dict(Counter(n.kind for n in nodes)), "health": health, "attention": att,
            "sources": sources, "capacity": cap}
