import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from sporemap.db import get_engine, migrate
from sporemap.db.models import Edge, Node, Observation, Override, Scan


def test_migrate_makes_tables():
    migrate()
    t = set(inspect(get_engine()).get_table_names())
    assert {"scan", "node", "observation", "edge", "override", "alembic_version"} <= t


def test_migrate_twice_is_fine():
    migrate()
    migrate()


def test_roundtrip():
    migrate()
    with Session(get_engine()) as s:
        sc = Scan(source="proxmox")
        host = Node(kind="host", name="ghostprox", attrs={"ip": "192.168.1.10"})
        ct = Node(kind="workload", name="pubsite")
        s.add_all([sc, host, ct])
        s.commit()
        s.add(Observation(scan_id=sc.id, node_id=ct.id, source="proxmox", ext_id="lxc/202",
                          kind="workload", attrs={"vmid": 202}, keys=["pve:ghostprox/202"]))
        s.add(Edge(kind="hosts", src_id=host.id, dst_id=ct.id, source="proxmox"))
        s.add(Override(node_id=ct.id, field="icon", value={"icon": "nextjs"}))
        s.commit()

        o = s.exec(select(Observation)).one()
        assert o.keys == ["pve:ghostprox/202"]
        assert s.exec(select(Edge)).one().dst_id == ct.id
        assert host.attrs["ip"] == "192.168.1.10"


def test_no_duplicate_edges():
    migrate()
    with Session(get_engine()) as s:
        a, b = Node(kind="host", name="a"), Node(kind="workload", name="b")
        s.add_all([a, b])
        s.commit()
        s.add(Edge(kind="hosts", src_id=a.id, dst_id=b.id, source="x"))
        s.add(Edge(kind="hosts", src_id=a.id, dst_id=b.id, source="y"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_foreign_keys_on():
    migrate()
    with Session(get_engine()) as s:
        s.add(Edge(kind="hosts", src_id=999, dst_id=998, source="x"))
        with pytest.raises(IntegrityError):
            s.commit()
