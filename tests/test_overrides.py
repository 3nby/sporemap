import pytest
from fastapi.testclient import TestClient

from sporemap.api.app import create_app


@pytest.fixture
def c():
    c = TestClient(create_app())
    c.post("/api/v1/scans", json={"source": "demo"})
    return c


def _ids(c):
    return {n["name"]: n["id"] for n in c.get("/api/v1/graph").json()["nodes"]}


def test_pin_roundtrip(c):
    nid = _ids(c)["web"]
    res = c.put("/api/v1/overrides/" + str(nid) + "/pos", json={"value": {"x": 10, "y": 20, "in": "5"}})
    assert res.status_code == 200 and res.json() == {"node_id": nid, "field": "pos", "value": {"x": 10, "y": 20, "in": "5"}}
    # putting again replaces, never a second row
    c.put("/api/v1/overrides/" + str(nid) + "/pos", json={"value": {"x": 30, "y": 40, "in": "5"}})
    assert c.get("/api/v1/overrides").json() == [{"node_id": nid, "field": "pos", "value": {"x": 30, "y": 40, "in": "5"}}]


def test_unpin_one_and_all(c):
    ids = _ids(c)
    for n in ("web", "proxy"):
        c.put("/api/v1/overrides/" + str(ids[n]) + "/pos", json={"value": {"x": 1, "y": 2}})
    assert c.delete("/api/v1/overrides/" + str(ids["web"]) + "/pos").status_code == 204
    assert [o["node_id"] for o in c.get("/api/v1/overrides").json()] == [ids["proxy"]]
    assert c.delete("/api/v1/overrides", params={"field": "pos"}).status_code == 204
    assert c.get("/api/v1/overrides").json() == []


def test_pins_survive_a_rescan(c):
    # node ids are stable across scans, so a pin keeps pointing at the same thing
    nid = _ids(c)["web"]
    c.put("/api/v1/overrides/" + str(nid) + "/pos", json={"value": {"x": 1, "y": 2}})
    c.post("/api/v1/scans", json={"source": "demo"})
    assert _ids(c)["web"] == nid
    assert c.get("/api/v1/overrides", params={"field": "pos"}).json()[0]["node_id"] == nid


def test_bad_field_and_node(c):
    assert c.put("/api/v1/overrides/1/color", json={"value": {}}).status_code == 400
    assert c.put("/api/v1/overrides/999/pos", json={"value": {}}).status_code == 404
