"""First cross-source chain: proxmox knows the CTs, caddy knows the routes."""
import asyncio
import json
from pathlib import Path

import httpx2
from test_correlate import snapshot
from test_proxmox import fake_api

from sporemap.core.correlate import correlate
from sporemap.core.scans import run_scan
from sporemap.db import migrate
from sporemap.sources.caddy import Caddy, CaddyConfig
from sporemap.sources.proxmox import Proxmox, ProxmoxConfig

FIX = Path(__file__).parent / "fixtures"


class FakePve(Proxmox):
    transport = fake_api("standalone.json")


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
