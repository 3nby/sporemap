import asyncio

from sqlmodel import Session, func, select
from test_correlate import make_source, scan, snapshot
from typer.testing import CliRunner

from sporemap.cli import app
from sporemap.core.correlate import correlate
from sporemap.core.scans import prune
from sporemap.db import get_engine, migrate
from sporemap.db.models import Node, Observation, RawLink, Scan
from sporemap.settings import get_settings
from sporemap.sources import Link, Obs, Result
from sporemap.sources.demo import Demo


def _count(m, *where):
    with Session(get_engine()) as s:
        return s.exec(select(func.count()).select_from(m).where(*where)).one()


def _scans(src):
    with Session(get_engine()) as s:
        return s.exec(select(Scan).where(Scan.source == src).order_by(Scan.id)).all()


def _yaml(monkeypatch, tmp_path, txt):
    f = tmp_path / "s.yaml"
    f.write_text(txt)
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    get_settings.cache_clear()


def flaky():
    # a source we can flip between ok and broken, and change what it sees
    st = {"boom": False, "extra": True}

    def res():
        if st["boom"]:
            raise RuntimeError("down")
        obs = [Obs("host", "h1", "box", {"cpu": 4}, ["mac:aa"]), Obs("app", "web", "nginx")]
        links = [Link("runs", "h1", "web")]
        if st["extra"]:
            obs.append(Obs("app", "db", "postgres"))
            links.append(Link("runs", "h1", "db"))
        return Result(obs, links)
    return {"f": make_source("f", res)}, st


def test_keeps_newest_n_and_drops_their_rows():
    migrate()
    srcs = {"demo": Demo}
    for _ in range(5):
        scan(srcs, "demo")
        correlate()
    ids = [sc.id for sc in _scans("demo")]
    r = prune(2)
    assert [sc.id for sc in _scans("demo")] == ids[-2:]
    assert r["scans"] == 3 and r["links"] == 3 * 8 and r["observations"] == 3 * 7
    assert _count(Observation) == 2 * 7 and _count(RawLink) == 2 * 8
    assert prune(2) == {"scans": 0, "observations": 0, "links": 0}  # nothing left to do


def test_latest_ok_survives_a_pile_of_failures():
    migrate()
    srcs, st = flaky()
    scan(srcs, "f")
    correlate()
    good = _scans("f")[-1].id
    before = snapshot()
    st["boom"] = True
    for _ in range(6):
        scan(srcs, "f")
    prune(3)
    left = _scans("f")
    assert len(left) == 4 and left[0].id == good and left[0].status == "ok"
    assert all(sc.status == "failed" for sc in left[1:])
    assert _count(Observation, Observation.scan_id == good) == 3
    assert _count(RawLink, RawLink.scan_id == good) == 2
    correlate()
    assert snapshot() == before


def test_graph_unchanged_after_pruning():
    migrate()
    srcs = {"demo": Demo}
    for _ in range(4):
        scan(srcs, "demo")
        correlate()
    with Session(get_engine()) as s:
        before = {(n.id, n.name, n.stale) for n in s.exec(select(Node)).all()}
    snap = snapshot()
    prune(1)
    assert snapshot() == snap
    correlate()
    assert snapshot() == snap
    with Session(get_engine()) as s:
        assert {(n.id, n.name, n.stale) for n in s.exec(select(Node)).all()} == before


def test_node_comes_back_with_its_old_id_after_its_scans_are_pruned():
    # the correlator gives a returning thing its old node id from older observations,
    # so pruning keeps the last sighting of each thing even when its scan is old
    migrate()
    srcs, st = flaky()
    scan(srcs, "f")
    correlate()
    with Session(get_engine()) as s:
        db_id = s.exec(select(Node.id).where(Node.name == "postgres")).one()
    st["extra"] = False
    for _ in range(4):
        scan(srcs, "f")
        correlate()
    prune(2)
    assert len(_scans("f")) == 3  # 2 newest + the old one holding postgres's last sighting
    assert _count(Observation, Observation.ext_id == "db") == 1
    assert _count(RawLink, RawLink.scan_id == _scans("f")[0].id) == 0  # its links still go
    st["extra"] = True
    scan(srcs, "f")
    correlate()
    with Session(get_engine()) as s:
        pg = s.exec(select(Node).where(Node.name == "postgres")).all()
    assert [n.id for n in pg] == [db_id] and not pg[0].stale


def test_sources_are_pruned_separately():
    migrate()
    srcs, _ = flaky()
    srcs["demo"] = Demo
    for _ in range(3):
        scan(srcs, "f")
    scan(srcs, "demo")
    correlate()
    prune(1)
    assert len(_scans("f")) == 1 and len(_scans("demo")) == 1


def test_keep_scans_setting(monkeypatch, tmp_path):
    assert get_settings().keep_scans == 50
    _yaml(monkeypatch, tmp_path, "keep_scans: off\n")
    assert get_settings().keep_scans is None
    migrate()
    for _ in range(3):
        scan({"demo": Demo}, "demo")
    assert prune()["scans"] == 0 and len(_scans("demo")) == 3
    _yaml(monkeypatch, tmp_path, "keep_scans: 2\n")
    assert prune()["scans"] == 1


def test_keep_scans_bad_value(monkeypatch, tmp_path):
    import pytest
    from pydantic import ValidationError

    _yaml(monkeypatch, tmp_path, "keep_scans: -3\n")
    with pytest.raises(ValidationError):
        get_settings()
    _yaml(monkeypatch, tmp_path, "keep_scans: 0\n")  # back to sane so teardown can open the db
    assert get_settings().keep_scans is None


def test_tick_prunes(monkeypatch, tmp_path):
    from sporemap.core.schedule import Scheduler

    _yaml(monkeypatch, tmp_path, "keep_scans: 2\nsources:\n  demo:\n")
    migrate()
    for _ in range(4):
        scan({"demo": Demo}, "demo")
    with Session(get_engine()) as s:  # make them old so the scheduler thinks demo is due
        for sc in s.exec(select(Scan)).all():
            sc.started_at = sc.started_at.replace(year=2000)
            s.add(sc)
        s.commit()
    assert asyncio.run(Scheduler({"demo": Demo}).tick()) == ["demo"]
    assert len(_scans("demo")) == 2


def test_cli_scan_prunes_and_prune_command(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "keep_scans: 2\nsources:\n  demo:\n")
    for _ in range(2):
        assert CliRunner().invoke(app, ["scan", "demo"]).exit_code == 0
    r = CliRunner().invoke(app, ["scan"])
    assert r.exit_code == 0, r.output
    assert "pruned 1 scans" in r.output and len(_scans("demo")) == 2
    r = CliRunner().invoke(app, ["prune", "--keep", "1"])
    assert r.exit_code == 0, r.output
    assert "pruned 1 scans, 7 observations, 8 links" in r.output and "db file:" in r.output
    assert len(_scans("demo")) == 1
    assert CliRunner().invoke(app, ["prune", "--keep", "0"]).exit_code == 1
