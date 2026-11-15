import asyncio
from pathlib import Path

import pytest

from sporemap.sources import cloudflared as cfd
from sporemap.sources.cloudflared import Cloudflared, CloudflaredConfig, parse

FIX = Path(__file__).parent / "fixtures" / "cloudflared"
TUN = "1e23e66b-97f2-4530-ad2b-6098e07a82d1"


def links(res):
    return sorted([ln.kind, ln.src, ln.dst] for ln in res.links)


def test_real_config_ghostprox():
    res = parse((FIX / "ghostprox.yml").read_text(), "10.17.48.106")
    t = res.obs[0]
    assert t.ext_id == "tunnel/" + TUN and "cftunnel:" + TUN in t.keys
    assert t.attrs["routes"]["proxmox.ghaw.net"] == "https://localhost:8006"
    hosts = sorted(o.name for o in res.obs if o.kind == "endpoint")
    assert hosts == sorted(["foundry.ghaw.net", "proxmox.ghaw.net", "ssh.ghaw.net", "ghaw.net",
                            "www.ghaw.net", "fileshare.ghaw.net", "drop.ghaw.net"])
    out = [ln for ln in links(res) if ln[1] == "tunnel/" + TUN]
    assert out == sorted([
        ["routes_to", "tunnel/" + TUN, "ip:192.168.1.69:30000"],
        ["routes_to", "tunnel/" + TUN, "ip:10.17.48.106:8006"],
        ["routes_to", "tunnel/" + TUN, "ip:10.17.48.106:22"],
        ["routes_to", "tunnel/" + TUN, "ip:192.168.1.70:80"],
        ["routes_to", "tunnel/" + TUN, "ip:192.168.1.10:8080"],
        ["routes_to", "tunnel/" + TUN, "ip:192.168.1.10:8081"],
    ])
    assert ["runs", "ip:10.17.48.106", "tunnel/" + TUN] in links(res)
    assert res.warnings == []


def test_localhost_without_host_ip_warns():
    res = parse((FIX / "ghostprox.yml").read_text(), None)
    assert sum("set host_ip" in w for w in res.warnings) == 2
    assert not any(ln.kind == "runs" for ln in res.links)


def test_named_tunnel_has_no_uuid_key():
    res = parse("tunnel: mytunnel\ningress:\n  - service: http_status:404\n", None)
    assert res.obs[0].keys == []


def test_missing_tunnel_line():
    with pytest.raises(ValueError, match="no tunnel"):
        parse("ingress: []\n", None)


class _Conn:
    def __init__(self, out):
        self.out = out
        self.ran = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def run(self, cmd, **kw):
        self.ran = cmd
        return type("R", (), {"stdout": self.out})()


def test_fetch_over_ssh(monkeypatch):
    got = {}
    conn = _Conn((FIX / "ghostprox.yml").read_text())

    def fake_connect(host, **kw):
        got.update(kw, host=host)
        return conn

    monkeypatch.setattr(cfd.asyncssh, "connect", fake_connect)
    cfg = CloudflaredConfig(host="10.17.48.106", key_path="~/.ssh/sporemap")
    res = asyncio.run(Cloudflared().discover(cfg))
    assert got["host"] == "10.17.48.106" and got["username"] == "sporemap"
    assert not got["client_keys"][0].startswith("~")  # expanded
    assert got["known_hosts"] == ()  # strict host checking with the normal known_hosts
    assert conn.ran == "cat /etc/cloudflared/config.yml"
    assert ["runs", "ip:10.17.48.106", "tunnel/" + TUN] in links(res)  # host_ip defaulted from host
