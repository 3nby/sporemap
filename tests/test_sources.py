import asyncio
from importlib.metadata import EntryPoint

import pytest

from sporemap.sources import Link, Obs, Result, load_sources
from sporemap.sources import loader as ld
from sporemap.sources.demo import Demo


def test_demo_is_registered():
    assert load_sources()["demo"] is Demo


def test_demo_discover_is_valid():
    d = Demo()
    asyncio.run(d.test(d.Config()))
    r = asyncio.run(d.discover(d.Config()))
    assert len(r.obs) == 7
    assert any(ln.dst.startswith("ip:") for ln in r.links)


def _fake_eps(monkeypatch, eps):
    monkeypatch.setattr(ld, "entry_points", lambda group: eps)


def test_broken_plugins_are_skipped(monkeypatch, caplog):
    _fake_eps(monkeypatch, [
        EntryPoint("good", "sporemap.sources.demo:Demo", ld.GROUP),
        EntryPoint("missing", "no_such_pkg.thing:X", ld.GROUP),
        EntryPoint("notasource", "sporemap.sources.base:Obs", ld.GROUP),
    ])
    out = load_sources()
    assert list(out) == ["good"]
    assert "missing" in caplog.text and "notasource" in caplog.text


def test_duplicate_names_keep_first(monkeypatch):
    _fake_eps(monkeypatch, [
        EntryPoint("demo", "sporemap.sources.demo:Demo", ld.GROUP),
        EntryPoint("demo", "sporemap.sources.base:Obs", ld.GROUP),
    ])
    assert load_sources()["demo"] is Demo


@pytest.mark.parametrize("res,msg", [
    (Result([Obs("toaster", "a", "a")]), "bad node kind"),
    (Result([Obs("host", "a", "a"), Obs("host", "a", "b")]), "duplicate ext_id"),
    (Result([Obs("host", "a", "a")], [Link("likes", "a", "a")]), "bad edge kind"),
    (Result([Obs("host", "a", "a")], [Link("hosts", "zzz", "a")]), "unknown ext_id"),
    (Result([Obs("host", "a", "a")], [Link("runs", "ip:1.2.3.4", "ip:5.6.7.8")]), "at least one end"),
    (Result([Obs("host", "a", "a")], [Link("hosts", "a", "nope")]), "unknown ext_id"),
])
def test_result_check_catches_bugs(res, msg):
    with pytest.raises(ValueError, match=msg):
        res.check()


def test_link_to_key_is_ok():
    Result([Obs("app", "a", "a")], [Link("routes_to", "a", "ip:10.0.0.5:80")]).check()


def test_link_from_key_is_ok():
    Result([Obs("app", "a", "a")], [Link("runs", "ip:10.0.0.5", "a")]).check()
