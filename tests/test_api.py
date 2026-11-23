import pytest
from fastapi.testclient import TestClient

from sporemap.api.app import create_app


@pytest.fixture
def c():
    return TestClient(create_app())


def test_empty_graph(c):
    assert c.get("/api/v1/graph").json() == {"nodes": [], "edges": []}


def test_scan_then_graph(c):
    res = c.post("/api/v1/scans", json={"source": "demo"})
    assert res.status_code == 201
    sc = res.json()
    assert sc["status"] == "ok" and sc["source"] == "demo"

    g = c.get("/api/v1/graph").json()
    assert len(g["nodes"]) == 7 and len(g["edges"]) == 8
    names = {n["id"]: n["name"] for n in g["nodes"]}
    assert ["pve1", "proxy"] in [[names[e["src"]], names[e["dst"]]] for e in g["edges"] if e["kind"] == "hosts"]


def test_graph_kind_filter_drops_dangling_edges(c):
    c.post("/api/v1/scans", json={"source": "demo"})
    g = c.get("/api/v1/graph", params={"kind": "workload"}).json()
    assert {n["name"] for n in g["nodes"]} == {"proxy", "web"}
    assert g["edges"] == []


def test_node_detail_says_who_saw_it(c):
    c.post("/api/v1/scans", json={"source": "demo"})
    g = c.get("/api/v1/graph").json()
    pve = next(n for n in g["nodes"] if n["name"] == "pve1")
    d = c.get("/api/v1/nodes/" + str(pve["id"])).json()
    assert d["name"] == "pve1" and d["kind"] == pve["kind"] and d["attrs"] == pve["attrs"]
    assert [(x["source"], x["ext_id"]) for x in d["seen_by"]] == [("demo", "node/pve1")]
    assert d["seen_by"][0]["seen_at"]


def test_node_detail_only_counts_latest_scan(c):
    # scan twice: still one entry per source, not one per scan
    c.post("/api/v1/scans", json={"source": "demo"})
    c.post("/api/v1/scans", json={"source": "demo"})
    nid = c.get("/api/v1/graph").json()["nodes"][0]["id"]
    assert len(c.get("/api/v1/nodes/" + str(nid)).json()["seen_by"]) == 1


def test_scan_all_runs_every_configured_source(monkeypatch, tmp_path):
    from sporemap.settings import get_settings

    p = tmp_path / "cfg.yaml"
    p.write_text("sources:\n  demo:\n  nosuch:\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(p))
    get_settings.cache_clear()
    c = TestClient(create_app())
    res = c.post("/api/v1/scans/all")
    assert res.status_code == 200
    by = {x["name"]: x for x in res.json()}
    assert by["demo"]["scan"]["status"] == "ok" and by["demo"]["error"] is None
    assert by["nosuch"]["scan"] is None and by["nosuch"]["error"] == "not installed"  # one bad source doesnt stop the rest
    assert len(c.get("/api/v1/graph").json()["nodes"]) == 7
    assert c.get("/api/v1/schedule").json()["running"] == []  # cleared again after


def test_node_detail_404(c):
    assert c.get("/api/v1/nodes/999").status_code == 404


def test_unknown_source_404(c):
    assert c.post("/api/v1/scans", json={"source": "nope"}).status_code == 404


def test_scan_list_and_get(c):
    sid = c.post("/api/v1/scans", json={"source": "demo"}).json()["id"]
    assert c.get("/api/v1/scans").json()[0]["id"] == sid
    assert c.get("/api/v1/scans/" + str(sid)).json()["status"] == "ok"
    assert c.get("/api/v1/scans/999").status_code == 404


def test_root_without_ui_explains(monkeypatch, tmp_path):
    from sporemap.api import app as appmod
    monkeypatch.setattr(appmod, "UI", tmp_path / "nothing")
    r = TestClient(create_app()).get("/")
    assert r.status_code == 200 and "npm run build" in r.text


def test_root_serves_built_ui(monkeypatch, tmp_path):
    from sporemap.api import app as appmod
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(appmod, "UI", tmp_path)
    c = TestClient(create_app())
    assert c.get("/").text == "<div id=root></div>"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/v1/graph").json() == {"nodes": [], "edges": []}  # api still wins
    assert c.get("/api/v1/nope").status_code == 404


def test_ui_caching(monkeypatch, tmp_path):
    # hashed assets cache forever, index.html always revalidates so an update never strands a browser
    from sporemap.api import app as appmod
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)")
    monkeypatch.setattr(appmod, "UI", tmp_path)
    c = TestClient(create_app())
    assert c.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in c.get("/assets/index-abc123.js").headers["cache-control"]
    assert c.get("/").headers.get("etag")  # so no-cache is a cheap 304 most of the time
