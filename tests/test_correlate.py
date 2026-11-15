import asyncio
import json
from pathlib import Path

from pydantic import BaseModel
from sqlmodel import Session, select

from sporemap.core.correlate import _strip_port, correlate
from sporemap.core.scans import run_scan
from sporemap.db import get_engine, migrate
from sporemap.db.models import Edge, Node
from sporemap.sources import Link, Obs, Result, Source
from sporemap.sources.demo import Demo

GOLDEN = Path(__file__).parent / "golden"


class _Cfg(BaseModel):
    pass


def make_source(name, result_fn):
    class S(Source):
        Config = _Cfg

        async def test(self, cfg):
            return None

        async def discover(self, cfg):
            return result_fn()

    S.name = name
    return S


def scan(srcs, name):
    asyncio.run(run_scan(name, sources=srcs))


def snapshot():
    # graph as plain names so it can be compared to a json file
    with Session(get_engine()) as s:
        nodes = {n.id: n for n in s.exec(select(Node)).all()}
        edges = s.exec(select(Edge)).all()
    return {
        "nodes": sorted([n.kind, n.name] for n in nodes.values() if not n.stale),
        "edges": sorted([e.kind, nodes[e.src_id].name, nodes[e.dst_id].name] for e in edges),
    }


def test_demo_matches_golden():
    migrate()
    scan({"demo": Demo}, "demo")
    correlate()
    want = json.loads((GOLDEN / "demo.json").read_text())
    assert snapshot() == want


def test_rescan_keeps_node_ids():
    migrate()
    scan({"demo": Demo}, "demo")
    correlate()
    with Session(get_engine()) as s:
        before = {n.name: n.id for n in s.exec(select(Node)).all()}
    scan({"demo": Demo}, "demo")
    correlate()
    with Session(get_engine()) as s:
        after = {n.name: n.id for n in s.exec(select(Node)).all()}
    assert before == after


def test_strong_key_merges_across_sources():
    migrate()
    a = make_source("a", lambda: Result([Obs("host", "h1", "pve1", {"cpu": 8}, ["mac:aa"])]))
    b = make_source("b", lambda: Result([Obs("host", "x", "pve1.lan", {"ram": 64}, ["mac:aa"])]))
    srcs = {"a": a, "b": b}
    scan(srcs, "a")
    scan(srcs, "b")
    correlate()
    with Session(get_engine()) as s:
        hosts = s.exec(select(Node).where(Node.kind == "host")).all()
    assert len(hosts) == 1
    assert hosts[0].attrs == {"cpu": 8, "ram": 64}


def test_weak_key_does_not_merge():
    migrate()
    a = make_source("a", lambda: Result([Obs("host", "h1", "one", {}, ["ip:10.0.0.5"])]))
    b = make_source("b", lambda: Result([Obs("host", "h2", "two", {}, ["ip:10.0.0.5"])]))
    srcs = {"a": a, "b": b}
    scan(srcs, "a")
    scan(srcs, "b")
    correlate()
    assert len(snapshot()["nodes"]) == 2


def test_different_kinds_never_merge():
    migrate()
    a = make_source("a", lambda: Result([Obs("host", "h", "h", {}, ["mac:aa"]),
                                         Obs("workload", "w", "w", {}, ["mac:aa"])]))
    scan({"a": a}, "a")
    correlate()
    assert len(snapshot()["nodes"]) == 2


def test_port_falls_back_to_ip():
    migrate()
    a = make_source("a", lambda: Result(
        [Obs("app", "proxy", "Caddy"), Obs("workload", "ct", "web", {}, ["ip:10.0.0.9"])],
        [Link("routes_to", "proxy", "ip:10.0.0.9:3000")]))
    scan({"a": a}, "a")
    correlate()
    assert snapshot()["edges"] == [["routes_to", "Caddy", "web"]]


def test_unresolved_key_makes_one_placeholder():
    migrate()
    a = make_source("a", lambda: Result([Obs("app", "proxy", "Caddy")],
                                        [Link("routes_to", "proxy", "ip:10.9.9.9:80")]))
    scan({"a": a}, "a")
    correlate()
    scan({"a": a}, "a")
    correlate()
    with Session(get_engine()) as s:
        ph = s.exec(select(Node).where(Node.kind == "external")).all()
    assert len(ph) == 1 and ph[0].attrs["placeholder"] == "ip:10.9.9.9:80" and ph[0].confidence < 1


def test_vanished_node_goes_stale_and_edge_goes_away():
    migrate()
    full = Result([Obs("host", "h", "pve1"), Obs("workload", "w", "old-ct")], [Link("hosts", "h", "w")])
    state = {"r": full}
    a = make_source("a", lambda: state["r"])
    scan({"a": a}, "a")
    correlate()
    state["r"] = Result([Obs("host", "h", "pve1")])
    scan({"a": a}, "a")
    correlate()
    with Session(get_engine()) as s:
        ct = s.exec(select(Node).where(Node.name == "old-ct")).one()
    assert ct.stale is True
    assert snapshot() == {"nodes": [["host", "pve1"]], "edges": []}


def test_failed_scan_keeps_last_good_data():
    migrate()
    state = {"fail": False}

    def res():
        if state["fail"]:
            raise RuntimeError("down")
        return Result([Obs("host", "h", "pve1")])

    a = make_source("a", res)
    scan({"a": a}, "a")
    correlate()
    state["fail"] = True
    scan({"a": a}, "a")
    correlate()
    assert snapshot()["nodes"] == [["host", "pve1"]]


def test_strip_port():
    assert _strip_port("ip:10.0.0.1:80") == "ip:10.0.0.1"
    assert _strip_port("ip:10.0.0.1") is None
    assert _strip_port("fqdn:a.b") is None


def test_link_from_key_across_sources():
    # proxmox knows the CT, caddy only knows "whatever has my ip runs me"
    migrate()
    pve = make_source("pve", lambda: Result([Obs("workload", "lxc/200", "caddy", {}, ["ip:192.168.1.70"])]))
    cad = make_source("cad", lambda: Result([Obs("app", "app/caddy", "Caddy")],
                                            [Link("runs", "ip:192.168.1.70", "app/caddy")]))
    srcs = {"pve": pve, "cad": cad}
    scan(srcs, "pve")
    scan(srcs, "cad")
    correlate()
    assert snapshot()["edges"] == [["runs", "caddy", "Caddy"]]


def test_unresolved_src_key_makes_placeholder():
    migrate()
    cad = make_source("cad", lambda: Result([Obs("app", "app/caddy", "Caddy")],
                                            [Link("runs", "ip:10.9.9.9", "app/caddy")]))
    scan({"cad": cad}, "cad")
    correlate()
    assert snapshot()["edges"] == [["runs", "ip:10.9.9.9", "Caddy"]]
