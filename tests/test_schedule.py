import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sporemap.api.app import create_app
from sporemap.core.scans import run_scan, scan_lock
from sporemap.core.schedule import Scheduler
from sporemap.db import migrate
from sporemap.settings import get_settings, parse_every
from sporemap.sources.config import build_config
from sporemap.sources.demo import Demo


def _yaml(monkeypatch, tmp_path, txt):
    p = tmp_path / "cfg.yaml"
    p.write_text(txt)
    monkeypatch.setenv("SPOREMAP_CONFIG", str(p))
    get_settings.cache_clear()


@pytest.mark.parametrize("v,want", [
    (90, 90.0), ("90", 90.0), ("30s", 30.0), ("15m", 900.0), ("2h", 7200.0), ("1d", 86400.0),
    ("1.5m", 90.0), (0, None), ("off", None), ("OFF", None), (False, None), (None, None),
])
def test_parse_every(v, want):
    assert parse_every(v) == want


@pytest.mark.parametrize("v", ["soon", "5x", True])
def test_parse_every_bad(v):
    with pytest.raises(ValueError):
        parse_every(v)


def test_intervals_from_yaml(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "scan_every: 10m\nsources:\n  demo:\n  fast:\n    scan_every: 30s\n"
                                 "  manual:\n    scan_every: off\n")
    assert Scheduler().intervals() == {"demo": 600.0, "fast": 30.0, "manual": None}


def test_default_is_15m(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    assert Scheduler().intervals() == {"demo": 900.0}


def test_bad_interval_fails_at_startup(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n    scan_every: whenever\n")
    with pytest.raises(ValidationError):
        get_settings()
    _yaml(monkeypatch, tmp_path, "")  # so conftest teardown can still load settings


def test_source_config_ignores_scan_every(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n    scan_every: 5m\n")
    assert "scan_every" not in build_config("demo", Demo).model_dump()


def test_tick_scans_due_then_waits(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n  manual:\n    scan_every: off\n")
    migrate()
    sch = Scheduler({"demo": Demo, "manual": Demo})

    async def go():
        assert await sch.tick() == ["demo"]  # never scanned, so due now
        assert await sch.tick() == []  # not again for 15m
        assert 1 <= sch.nap() <= 60
    asyncio.run(go())

    st = sch.status()
    by = {x["name"]: x for x in st["sources"]}
    assert by["demo"]["last_run"] is not None and by["demo"]["next_run"] > by["demo"]["last_run"]
    assert by["manual"]["every"] is None and by["manual"]["next_run"] is None


def test_manual_scan_pushes_next_back(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    migrate()
    asyncio.run(run_scan("demo", sources={"demo": Demo}))  # like POST /scans or the cli
    assert asyncio.run(Scheduler({"demo": Demo}).tick()) == []  # fresh scheduler, as after a restart


def test_broken_source_doesnt_spin(monkeypatch, tmp_path):
    # not installed = no Scan row, it still has to wait its turn
    _yaml(monkeypatch, tmp_path, "sources:\n  ghost:\n")
    migrate()
    sch = Scheduler({})

    async def go():
        assert await sch.tick() == ["ghost"]
        assert await sch.tick() == []
    asyncio.run(go())


def test_tick_waits_for_api_scan(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    migrate()
    sch = Scheduler({"demo": Demo})

    async def go():
        async with scan_lock():  # pretend POST /scans is mid scan
            t = asyncio.create_task(sch.tick())
            await asyncio.sleep(0.05)
            assert not t.done()
        return await t
    assert asyncio.run(go()) == ["demo"]


def _wait_for_run(c, n=100):
    # last_run shows up as soon as the scan row exists, so also wait for it to finish
    for _ in range(n):
        st = c.get("/api/v1/schedule").json()
        if st["sources"] and st["sources"][0]["last_run"] and not st["running"]:
            return st
        time.sleep(0.05)
    return st


def test_serve_scans_on_startup(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    with TestClient(create_app({"demo": Demo})) as c:  # with = lifespan runs
        st = _wait_for_run(c)
        assert st["enabled"] is True
        assert st["sources"][0]["name"] == "demo" and st["sources"][0]["every"] == 900
        assert st["sources"][0]["last_run"] is not None
        assert len(c.get("/api/v1/graph").json()["nodes"]) == 7


def test_no_schedule(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    monkeypatch.setenv("SPOREMAP_SCHEDULE", "false")
    with TestClient(create_app({"demo": Demo})) as c:
        time.sleep(0.2)
        st = c.get("/api/v1/schedule").json()
        assert st["enabled"] is False and st["sources"][0]["last_run"] is None
        assert c.get("/api/v1/graph").json()["nodes"] == []
