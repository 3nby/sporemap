import asyncio
import json
from pathlib import Path

import httpx2
import pytest

from sporemap.sources import docker as dk
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


# ---- ssh tunnel mode

class _Lis:
    def __init__(self):
        self.closed = False

    def get_port(self):
        return 40123

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, log, host):
        self.log, self.host = log, host
        self.lis = _Lis()

    async def __aenter__(self):
        self.log.append(("open", self.host))
        return self

    async def __aexit__(self, *a):
        self.log.append(("close", self.host))
        return False

    async def forward_local_port(self, lhost, lport, rhost, rport):
        self.log.append(("fwd", lhost, lport, rhost, rport))
        return self.lis


def fake_ssh(monkeypatch):
    log, conns = [], {}

    def connect(host, **kw):
        conns[host] = c = _Conn(log, host)
        c.kw = kw
        return c

    monkeypatch.setattr(dk.asyncssh, "connect", connect)
    return log, conns


def run_ssh(monkeypatch, cfg_kw=None, **host_kw):
    log, conns = fake_ssh(monkeypatch)
    seen = []
    d = Docker()
    d.transport = fake_docker(seen=seen)
    cfg = DockerConfig(hosts=[DockerHost(name="fileshare", **host_kw)], **(cfg_kw or {"key_path": "~/.ssh/sporemap"}))
    res = asyncio.run(d.discover(cfg))
    return res, log, conns, seen


def test_ssh_tunnel_direct(monkeypatch):
    res, log, conns, seen = run_ssh(monkeypatch, ssh="10.17.48.126")
    c = conns["10.17.48.126"]
    assert c.kw["username"] == "sporemap" and c.kw["port"] == 22
    assert not c.kw["client_keys"][0].startswith("~")
    assert c.kw["known_hosts"] == ()  # strict checking against the normal known_hosts
    assert "tunnel" not in c.kw
    assert ("fwd", "127.0.0.1", 0, "127.0.0.1", 2375) in log  # proxy on the docker box's loopback
    assert {(r.url.host, r.url.port) for r in seen} == {("127.0.0.1", 40123)}  # went through the forward
    assert c.lis.closed and log[-1] == ("close", "10.17.48.126")
    # host_ip comes from the ssh host, so containers still land on the right box
    assert "ip:10.17.48.126:8080" in by_name(res)["files-web"].keys
    assert res.warnings == []


def test_ssh_tunnel_through_jump(monkeypatch):
    res, log, conns, _ = run_ssh(monkeypatch, ssh="192.168.1.10", jump="root@100.87.242.14:2222",
                                 host_ip="192.168.1.10")
    j, c = conns["100.87.242.14"], conns["192.168.1.10"]
    assert j.kw["username"] == "root" and j.kw["port"] == 2222
    assert c.kw["tunnel"] is j  # second hop rides the first
    assert c.kw["username"] == "sporemap"
    # inner closes before the jump
    assert [e for e in log if e[0] == "close"] == [("close", "192.168.1.10"), ("close", "100.87.242.14")]
    assert len(res.obs) == 4


def test_ssh_custom_remote_port_and_per_host_key(monkeypatch):
    _, log, conns, _ = run_ssh(monkeypatch, {"key_path": "~/default"}, ssh="ops@nas:2200",
                               url="http://127.0.0.1:2376", key_path="/keys/nas", host_ip="10.0.0.9")
    c = conns["nas"]
    assert c.kw["username"] == "ops" and c.kw["port"] == 2200
    assert c.kw["client_keys"] == ["/keys/nas"]
    assert ("fwd", "127.0.0.1", 0, "127.0.0.1", 2376) in log


def test_ssh_without_key_is_clear(monkeypatch):
    fake_ssh(monkeypatch)
    d = Docker()
    d.transport = fake_docker()
    cfg = DockerConfig(hosts=[DockerHost(name="nas", ssh="10.0.0.9")])
    with pytest.raises(RuntimeError, match="no key_path"):
        asyncio.run(d.discover(cfg))


def test_ssh_failure_is_a_warning(monkeypatch):
    fake_ssh(monkeypatch)
    real = dk.asyncssh.connect

    def connect(host, **kw):
        if host == "dead":
            raise ConnectionRefusedError("refused")
        return real(host, **kw)

    monkeypatch.setattr(dk.asyncssh, "connect", connect)
    d = Docker()
    d.transport = fake_docker()
    cfg = DockerConfig(key_path="k", hosts=[DockerHost(name="good", ssh="10.0.0.1"),
                                            DockerHost(name="bad", ssh="dead", host_ip="10.0.0.2")])
    res = asyncio.run(d.discover(cfg))
    assert {o.attrs["docker_host"] for o in res.obs} == {"good"}
    assert any(w.startswith("bad: ConnectionRefusedError") for w in res.warnings)


def test_target_parsing():
    assert dk._target("10.0.0.1", "sporemap") == ("sporemap", "10.0.0.1", 22)
    assert dk._target("me@box:2222", "sporemap") == ("me", "box", 2222)
    assert dk._target("fd00::5", "s") == ("s", "fd00::5", 22)
    assert dk._target("me@[fd00::5]:2200", "s") == ("me", "fd00::5", 2200)
