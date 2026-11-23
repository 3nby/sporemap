import asyncio

from fastapi.testclient import TestClient
from pydantic import BaseModel

from sporemap.api.app import create_app
from sporemap.core.correlate import correlate
from sporemap.core.scans import run_scan
from sporemap.db import migrate
from sporemap.sources import Link, Obs, Result, Source

G = 1024 ** 3
MAC = "bc:24:11:e4:cf:28"


class _Cfg(BaseModel):
    pass


class Lab(Source):
    # a small lab with one of everything the overview should notice
    name = "lab"
    Config = _Cfg

    async def test(self, cfg):
        return None

    async def discover(self, cfg):
        nic = [{"dev": "net0", "bridge": "vmbr0", "mac": MAC}]
        o = [
            Obs("host", "node/h1", "h1", {"status": "online", "cpus": 8, "mem": 16 * G, "ip": "10.0.0.1"}, ["pve:h1"]),
            Obs("workload", "lxc/100", "old", {"status": "stopped", "cpus": 2, "mem": G, "nics": nic, "ips": []}, ["pve:100", "mac:" + MAC]),
            Obs("workload", "lxc/102", "clone", {"status": "running", "cpus": 2, "mem": 2 * G, "nics": nic, "ips": ["10.0.0.5"]},
                ["pve:102", "mac:" + MAC]),
            Obs("workload", "lxc/103", "dup", {"status": "running", "cpus": 4, "mem": 4 * G, "ips": ["10.0.0.5"]}, ["pve:103"]),
            Obs("container", "ctr/db", "db", {"state": "running", "status": "Up 2 days (unhealthy)"}, ["docker:db"]),
            Obs("container", "ctr/web", "web", {"state": "running", "status": "Up 2 days"}, ["docker:web"]),
            Obs("external", "tunnel/t1", "main", {"type": "tunnel", "status": "down", "routes": {"x.lan": "http://10.0.0.5:80"}}, ["cftunnel:t1"]),
            Obs("external", "tunnel/t2", "unused", {"type": "tunnel", "status": "inactive", "routes": {}}, ["cftunnel:t2"]),
            Obs("endpoint", "dns/y.lan", "y.lan", {"type": "A", "content": "10.0.0.99"}, ["fqdn:y.lan"]),
            Obs("endpoint", "dns/z.lan", "z.lan", {"type": "CNAME", "content": "elsewhere"}, ["fqdn:z.lan"]),
        ]
        ln = [
            Link("hosts", "node/h1", "lxc/100"),
            Link("hosts", "node/h1", "lxc/102"),
            Link("hosts", "node/h1", "lxc/103"),
            Link("hosts", "lxc/102", "ctr/db"),
            Link("hosts", "lxc/102", "ctr/web"),
            Link("routes_to", "dns/z.lan", "ip:10.9.9.9:80"),  # nothing has that ip, so a placeholder
        ]
        return Result(o, ln).check()


class Broken(Lab):
    name = "broken"

    async def discover(self, cfg):
        raise RuntimeError("401 from the api")


def _ov():
    migrate()
    asyncio.run(run_scan("lab", sources={"lab": Lab}))
    asyncio.run(run_scan("broken", sources={"broken": Broken}))
    correlate()
    return TestClient(create_app()).get("/api/v1/overview").json()


def test_health_counts():
    h = _ov()["health"]
    assert h["workloads"] == [2, 3]  # old is stopped
    assert h["containers"] == [1, 2]  # db is unhealthy
    assert h["tunnels"] == [0, 2]
    assert h["sources"] == [1, 2]  # broken failed


def test_attention_finds_everything_worst_first():
    att = _ov()["attention"]
    got = {(a["level"], a["what"]) for a in att}
    assert ("bad", "container") in got  # unhealthy db
    assert ("bad", "tunnel") in got  # down and still routing
    assert ("info", "tunnel") in got  # dead but routes nothing, just clutter
    assert ("bad", "source") in got
    assert ("warn", "mac") in got  # the cloned CT, shown as two workloads (not merged)
    assert ("warn", "ip") in got  # clone and dup both claim 10.0.0.5
    assert ("warn", "unknown") in got  # the placeholder z.lan points at
    assert ("info", "workload") in got  # old is stopped
    assert ("info", "dns") in got  # y.lan leads nowhere
    assert ("info", "route") in got  # x.lan has a tunnel route but no dns record
    levels = [a["level"] for a in att]
    assert levels == sorted(levels, key=["bad", "warn", "info"].index)
    mac = next(a for a in att if a["what"] == "mac")
    assert "old" in mac["text"] and "clone" in mac["text"] and mac["node_id"]
    src = next(a for a in att if a["what"] == "source")
    assert "broken" in src["text"] and "401" in src["text"] and src["node_id"] is None


def test_capacity_counts_running_only():
    cap = _ov()["capacity"]
    assert len(cap) == 1
    h = cap[0]
    assert h["name"] == "h1" and h["cpus"] == 8 and h["mem"] == 16 * G
    assert h["cpus_running"] == 6 and h["mem_running"] == 6 * G
    assert h["cpus_all"] == 8 and h["mem_all"] == 7 * G
    assert (h["running"], h["total"]) == (2, 3)


def test_sources_listed():
    src = {s["name"]: s for s in _ov()["sources"]}
    assert src["lab"]["status"] == "ok" and src["lab"]["last_ok"]
    assert src["broken"]["status"] == "failed" and "401" in src["broken"]["error"] and src["broken"]["last_ok"] is None


def test_empty_lab():
    ov = TestClient(create_app()).get("/api/v1/overview").json()
    assert ov == {"counts": {}, "health": {"workloads": [0, 0], "containers": [0, 0], "tunnels": [0, 0], "sources": [0, 0]},
                  "attention": [], "sources": [], "capacity": []}
