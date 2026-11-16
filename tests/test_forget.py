from sqlmodel import Session, select
from test_correlate import make_source, scan, snapshot
from typer.testing import CliRunner

from sporemap.cli import app
from sporemap.core.correlate import correlate
from sporemap.core.scans import forget
from sporemap.db import get_engine, migrate
from sporemap.db.models import Node, Observation, Override, Scan
from sporemap.sources import Link, Obs, Result


def two_sources():
    a = make_source("a", lambda: Result(
        [Obs("host", "h1", "pve1", {"cpu": 8}, ["mac:aa"]), Obs("app", "only-a", "Caddy")],
        [Link("runs", "h1", "only-a"), Link("routes_to", "only-a", "ip:10.9.9.9:80")]))
    b = make_source("b", lambda: Result([Obs("host", "x", "pve1", {"ram": 64}, ["mac:aa"])]))
    srcs = {"a": a, "b": b}
    migrate()
    scan(srcs, "a")
    scan(srcs, "b")
    correlate()
    return srcs


def nodes():
    with Session(get_engine()) as s:
        return {n.name: n for n in s.exec(select(Node)).all()}


def test_forget_removes_only_that_sources_stuff():
    two_sources()
    host_id = nodes()["pve1"].id
    r = forget("a")
    assert r["scans"] == 1 and r["observations"] == 2
    n = nodes()
    assert sorted(n) == ["pve1"]  # Caddy and the ip placeholder were a's alone
    assert n["pve1"].id == host_id and n["pve1"].attrs == {"ram": 64}  # merged node survives, keeps its id
    assert snapshot()["edges"] == []
    assert r["removed_nodes"] == 2 and r["nodes"] == 1 and r["edges"] == 0
    with Session(get_engine()) as s:
        assert {sc.source for sc in s.exec(select(Scan)).all()} == {"b"}
        assert all(o.source == "b" for o in s.exec(select(Observation)).all())


def test_forget_drops_overrides_of_removed_nodes():
    two_sources()
    with Session(get_engine()) as s:
        s.add(Override(node_id=nodes()["Caddy"].id, field="name", value={"v": "proxy"}))
        s.add(Override(node_id=nodes()["pve1"].id, field="name", value={"v": "big box"}))
        s.commit()
    forget("a")
    with Session(get_engine()) as s:
        assert [o.value["v"] for o in s.exec(select(Override)).all()] == ["big box"]


def test_placeholder_another_source_still_uses_is_kept():
    srcs = two_sources()
    srcs["c"] = make_source("c", lambda: Result([Obs("app", "p2", "nginx")],
                                                [Link("routes_to", "p2", "ip:10.9.9.9:80")]))
    scan(srcs, "c")
    correlate()
    forget("a")
    assert "ip:10.9.9.9:80" in nodes()
    assert snapshot()["edges"] == [["routes_to", "nginx", "ip:10.9.9.9:80"]]


def test_stale_nodes_of_other_sources_stay():
    state = {"gone": False}
    b = make_source("b", lambda: Result([] if state["gone"] else [Obs("container", "c1", "old-thing")]))
    srcs = two_sources()
    srcs["b2"] = b
    scan(srcs, "b2")
    correlate()
    state["gone"] = True
    scan(srcs, "b2")
    correlate()
    forget("a")
    assert nodes()["old-thing"].stale  # still history from b2, not forget's business


def test_forget_unknown_source():
    migrate()
    assert forget("nope") is None


def test_cli_forget():
    two_sources()
    r = CliRunner().invoke(app, ["sources", "forget", "a"], input="n\n")
    assert r.exit_code == 1 and "Caddy" in nodes()  # said no, nothing happened
    r = CliRunner().invoke(app, ["sources", "forget", "a", "--yes"])
    assert r.exit_code == 0, r.output
    assert "forgot a: 1 scans, 2 observations, 2 nodes removed" in r.output
    assert "graph: 1 nodes, 0 edges" in r.output
    r = CliRunner().invoke(app, ["sources", "forget", "a", "-y"])
    assert "nothing stored for a" in r.output
