"""Cross-source chain: cloudflare knows dns, caddy knows routes, proxmox knows the CTs."""
import asyncio
import json
from pathlib import Path

import httpx2
from sqlmodel import Session, select
from test_cloudflare import fake_cf
from test_correlate import snapshot
from test_proxmox import fake_api

from sporemap.core.correlate import correlate
from sporemap.core.scans import run_scan
from sporemap.db import get_engine, migrate
from sporemap.db.models import Edge, Node
from sporemap.sources.caddy import Caddy, CaddyConfig
from sporemap.sources.cloudflare import Cloudflare, CloudflareConfig
from sporemap.sources.cloudflared import Cloudflared, CloudflaredConfig
from sporemap.sources.proxmox import Proxmox, ProxmoxConfig

FIX = Path(__file__).parent / "fixtures"


class FakePve(Proxmox):
    transport = fake_api("standalone.json")


class FakeCf(Cloudflare):
    transport = fake_cf()


class FakeCfd(Cloudflared):
    async def _fetch(self, cfg):
        return (FIX / "cloudflared" / "ghostprox.yml").read_text()


class FakeCaddy(Caddy):
    transport = httpx2.MockTransport(
        lambda req: httpx2.Response(200, json=json.loads((FIX / "caddy" / "ghostprox.json").read_text())))


def test_ghaw_net_reaches_pubsite():
    migrate()
    srcs = {"proxmox": FakePve, "caddy": FakeCaddy}
    pc = ProxmoxConfig(host="pve.test", token_id="a@pve!b", token_secret="x", verify_ssl=False)
    cc = CaddyConfig(url="http://caddy.test:2020", host_ip="192.168.1.70")
    asyncio.run(run_scan("proxmox", cfg=pc, sources=srcs))
    asyncio.run(run_scan("caddy", cfg=cc, sources=srcs))
    correlate()

    e = snapshot()["edges"]
    assert ["routes_to", "ghaw.net", "Caddy"] in e
    assert ["runs", "caddy", "Caddy"] in e  # caddy CT (from proxmox) runs the Caddy app
    assert ["routes_to", "Caddy", "pubsite"] in e  # 192.168.1.71:8080 resolved to the pubsite CT
    assert ["hosts", "ghostprox", "pubsite"] in e


def test_cloudflare_and_caddy_agree_on_ghaw_net():
    migrate()
    srcs = {"proxmox": FakePve, "caddy": FakeCaddy, "cloudflare": FakeCf}
    pc = ProxmoxConfig(host="pve.test", token_id="a@pve!b", token_secret="x", verify_ssl=False)
    asyncio.run(run_scan("proxmox", cfg=pc, sources=srcs))
    asyncio.run(run_scan("caddy", cfg=CaddyConfig(url="http://c.test", host_ip="192.168.1.70"), sources=srcs))
    asyncio.run(run_scan("cloudflare", cfg=CloudflareConfig(api_token="t"), sources=srcs))
    correlate()

    g = snapshot()
    assert g["nodes"].count(["endpoint", "ghaw.net"]) == 1  # dns record + caddy site merged by fqdn
    assert ["routes_to", "ghaw.net", "foundry"] in g["edges"]  # into the tunnel
    assert ["routes_to", "ghaw.net", "Caddy"] in g["edges"]  # and caddy serves it
    assert ["routes_to", "factions.ghaw.net", "ghaw.net"] in g["edges"]


def test_full_trace_internet_to_app():
    migrate()
    srcs = {"proxmox": FakePve, "caddy": FakeCaddy, "cloudflare": FakeCf, "cloudflared": FakeCfd}
    pc = ProxmoxConfig(host="pve.test", token_id="a@pve!b", token_secret="x", verify_ssl=False)
    asyncio.run(run_scan("proxmox", cfg=pc, sources=srcs))
    asyncio.run(run_scan("caddy", cfg=CaddyConfig(url="http://c.test", host_ip="192.168.1.70"), sources=srcs))
    asyncio.run(run_scan("cloudflare", cfg=CloudflareConfig(api_token="t"), sources=srcs))
    asyncio.run(run_scan("cloudflared", cfg=CloudflaredConfig(host="10.17.48.106", key_path="k"), sources=srcs))
    correlate()

    g = snapshot()
    e = g["edges"]
    assert g["nodes"].count(["external", "foundry"]) == 1  # api tunnel + config tunnel merged by cftunnel key
    # ghaw.net -> foundry tunnel -> Caddy -> pubsite
    assert ["routes_to", "ghaw.net", "foundry"] in e
    assert ["routes_to", "foundry", "Caddy"] in e  # 192.168.1.70:80 is Caddy's own listen key
    assert ["routes_to", "Caddy", "pubsite"] in e
    # proxmox.ghaw.net -> tunnel -> the proxmox host itself via localhost:8006
    assert ["routes_to", "foundry", "ghostprox"] in e
    # the tunnel and the CT are both called "foundry", so check kinds: tunnel -> CT at .69:30000
    with Session(get_engine()) as s:
        n = {x.id: x for x in s.exec(select(Node)).all()}
        kinds = {(n[x.src_id].kind, n[x.dst_id].kind, n[x.dst_id].name) for x in s.exec(select(Edge)).all()}
    assert ("external", "workload", "foundry") in kinds
    assert ["runs", "ghostprox", "foundry"] in e
