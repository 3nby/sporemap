import asyncio

import pytest
from pydantic import BaseModel
from sqlmodel import Session, select

from sporemap.core.scans import run_scan
from sporemap.db import get_engine, migrate
from sporemap.db.models import Observation, RawLink, Scan
from sporemap.sources import Obs, Result, Source
from sporemap.sources.demo import Demo


class _Cfg(BaseModel):
    pass


class Boom(Source):
    name = "boom"
    Config = _Cfg

    async def test(self, cfg):
        return None

    async def discover(self, cfg):
        raise RuntimeError("api said no")


class Slow(Boom):
    async def discover(self, cfg):
        await asyncio.sleep(5)


class Bad(Boom):
    async def discover(self, cfg):
        return Result([Obs("toaster", "x", "x")])


def _run(name, cls):
    migrate()
    return asyncio.run(run_scan(name, sources={name: cls}))


def test_demo_scan_stores_everything():
    sc = _run("demo", Demo)
    assert sc.status == "ok" and sc.error is None and sc.finished_at
    with Session(get_engine()) as s:
        obs = s.exec(select(Observation).where(Observation.scan_id == sc.id)).all()
        links = s.exec(select(RawLink).where(RawLink.scan_id == sc.id)).all()
    assert len(obs) == 7 and len(links) == 8
    pve = next(o for o in obs if o.ext_id == "node/pve1")
    assert pve.name == "pve1" and "ip:192.168.1.10" in pve.keys


def test_exception_marks_failed():
    sc = _run("boom", Boom)
    assert sc.status == "failed"
    assert "api said no" in sc.error


def test_timeout_marks_failed(monkeypatch):
    monkeypatch.setenv("SPOREMAP_SCAN_TIMEOUT", "0.1")
    from sporemap.settings import get_settings
    get_settings.cache_clear()
    sc = _run("slow", Slow)
    assert sc.status == "failed" and "timed out" in sc.error


def test_bad_result_marks_failed_and_stores_nothing():
    sc = _run("bad", Bad)
    assert sc.status == "failed" and "bad node kind" in sc.error
    with Session(get_engine()) as s:
        assert s.exec(select(Observation)).all() == []


def test_unknown_source_raises():
    migrate()
    with pytest.raises(KeyError):
        asyncio.run(run_scan("nope", sources={}))
    with Session(get_engine()) as s:
        assert s.exec(select(Scan)).all() == []


def test_cli_warns_when_source_not_configured():
    from typer.testing import CliRunner

    from sporemap.cli import app
    r = CliRunner().invoke(app, ["scan", "demo"])
    assert r.exit_code == 0
    assert "isn't configured" in r.output


def test_scan_all_keeps_going_after_failures():
    from sporemap.core.scans import scan_all

    migrate()
    srcs = {"demo": Demo, "boom": Boom}
    res, g = asyncio.run(scan_all(["boom", "demo", "ghost"], sources=srcs))
    by = {x["name"]: x for x in res}
    assert by["boom"]["scan"].status == "failed" and "api said no" in by["boom"]["error"]
    assert by["demo"]["scan"].status == "ok"
    assert by["ghost"]["scan"] is None and by["ghost"]["error"] == "not installed"
    assert g and g["nodes"] > 0  # demo still got correlated


def test_scan_all_nothing_ok_skips_correlate():
    from sporemap.core.scans import scan_all

    migrate()
    res, g = asyncio.run(scan_all(["boom"], sources={"boom": Boom}))
    assert g is None and res[0]["scan"].status == "failed"


def test_cli_scan_no_arg_uses_yaml(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from sporemap.cli import app
    from sporemap.settings import get_settings

    f = tmp_path / "s.yaml"
    f.write_text("sources:\n  demo:\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    get_settings.cache_clear()
    r = CliRunner().invoke(app, ["scan"])
    assert r.exit_code == 0, r.output
    assert "demo: ok, 7 observations, 8 links" in r.output
    assert "graph:" in r.output


def test_cli_scan_no_arg_nothing_configured():
    from typer.testing import CliRunner

    from sporemap.cli import app
    r = CliRunner().invoke(app, ["scan"])
    assert r.exit_code == 1
    assert "no sources configured" in r.output
