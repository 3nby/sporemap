import asyncio
import json
from pathlib import Path

import httpx2
import pytest

from sporemap.sources.docker import Docker, DockerConfig, DockerHost

FIX = Path(__file__).parent / "fixtures" / "docker"


def fake_docker(name="fileshare.json", overrides=None, seen=None):
    data = json.loads((FIX / name).read_text())
    data.update(overrides or {})

    def handler(req):
        if seen is not None:
            seen.append(req)
        v = data.get(req.url.path)
        if isinstance(v, int):
            return httpx2.Response(v)
        if v is None:
            return httpx2.Response(404)
        if req.url.path == "/containers/json" and req.url.params.get("all") == "false":
            v = [c for c in v if c["State"] == "running"]
        return httpx2.Response(200, json=v)

    return httpx2.MockTransport(handler)


def run(include_stopped=True, **kw):
    d = Docker()
    d.transport = fake_docker(**{k: kw.pop(k) for k in ("overrides", "seen") if k in kw})
    return asyncio.run(d.discover(DockerConfig(hosts=[DockerHost(name="fileshare", **kw)],
                                               include_stopped=include_stopped)))


def by_name(res):
    return {o.name: o for o in res.obs}


def test_fileshare_containers():
    res = run(host_ip="192.168.1.10")
    c = by_name(res)
    assert sorted(c) == ["dockerproxy", "drop", "files-web", "old-thing"]
    web = c["files-web"]
    assert web.kind == "container" and web.attrs["compose_project"] == "files"
    assert web.attrs["ports"] == [{"bind": "0.0.0.0", "host_port": 8080, "port": 80, "proto": "tcp"}]  # v6 dupe gone
    assert "ip:192.168.1.10:8080" in web.keys
    assert "ip:192.168.1.10:8081" in c["drop"].keys
    assert any(k.startswith("docker:ENG-FILESHARE-0001/") for k in web.keys)
    assert sorted((ln.kind, ln.src) for ln in res.links) == [("hosts", "ip:192.168.1.10")] * 4
    assert res.warnings == []


def test_loopback_binds_get_no_ip_key():
    proxy = by_name(run(host_ip="192.168.1.10"))["dockerproxy"]
    assert not any(k.startswith("ip:") for k in proxy.keys)
    assert proxy.attrs["ports"][0]["bind"] == "127.0.0.1"


def test_unpublished_ports_ignored():
    assert by_name(run(host_ip="10.0.0.1"))["old-thing"].attrs["ports"] == []


def test_running_only():
    assert "old-thing" not in by_name(run(host_ip="10.0.0.1", include_stopped=False))


def test_host_ip_from_url():
    res = run(url="http://10.17.48.108:2375")
    assert ["hosts", "ip:10.17.48.108"] in [[ln.kind, ln.src] for ln in res.links]


def test_localhost_url_without_host_ip_warns():
    res = run()  # default url is 127.0.0.1, could be a tunnel to anywhere
    assert res.links == []
    assert any("host_ip" in w for w in res.warnings)


def test_proxy_403_is_clear():
    with pytest.raises(RuntimeError, match="CONTAINERS=1"):
        run(overrides={"/containers/json": 403})


def test_one_dead_host_is_a_warning():
    def handler(req):
        if req.url.host == "dead.test":
            raise httpx2.ConnectError("nope")
        return fake_docker().handle_request(req)

    d = Docker()
    d.transport = httpx2.MockTransport(handler)
    cfg = DockerConfig(hosts=[DockerHost(name="good", url="http://good.test", host_ip="10.0.0.1"),
                              DockerHost(name="bad", url="http://dead.test", host_ip="10.0.0.2")])
    res = asyncio.run(d.discover(cfg))
    assert len(res.obs) == 4
    assert any(w.startswith("bad: ConnectError") for w in res.warnings)
    assert {o.attrs["docker_host"] for o in res.obs} == {"good"}
