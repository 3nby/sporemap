import asyncio
import json
from pathlib import Path

import httpx2
import pytest

from sporemap.sources.cloudflare import Cloudflare, CloudflareConfig, service_key

FIX = Path(__file__).parent / "fixtures" / "cloudflare"
FOUNDRY = "1e23e66b-97f2-4530-ad2b-6098e07a82d1"
FILESHARE = "5c0ffee0-0000-4000-8000-00000000f11e"


def fake_cf(overrides=None, page_size=None, seen=None):
    data = json.loads((FIX / "ghaw.json").read_text())
    data.update(overrides or {})

    def handler(req):
        if seen is not None:
            seen.append(req)
        path = req.url.path.removeprefix("/client/v4")
        v = data.get(path)
        if isinstance(v, int):
            return httpx2.Response(v, json={"success": False, "errors": [{"message": "nope"}]})
        if v is None:
            return httpx2.Response(404, json={"success": False})
        if isinstance(v, dict):  # single object endpoints like /configurations
            return httpx2.Response(200, json={"success": True, "result": v})
        # paginate lists so the paging loop gets exercised
        n = page_size or 100
        page = int(req.url.params.get("page", "1"))
        chunk = v[(page - 1) * n: page * n]
        pages = max(1, -(-len(v) // n))
        return httpx2.Response(200, json={"success": True, "result": chunk,
                                          "result_info": {"page": page, "total_pages": pages}})

    return httpx2.MockTransport(handler)


def run(cfg_extra=None, **kw):
    c = Cloudflare()
    c.transport = fake_cf(**kw)
    return asyncio.run(c.discover(CloudflareConfig(api_token="tok", **(cfg_extra or {}))))


def links(res):
    return sorted([ln.kind, ln.src, ln.dst] for ln in res.links)


def test_ghaw_zone():
    res = run()
    assert sorted(o.ext_id for o in res.obs) == sorted([
        "tunnel/" + FOUNDRY, "tunnel/" + FILESHARE,
        "dns/ghaw.net", "dns/foundry.ghaw.net", "dns/proxmox.ghaw.net", "dns/ssh.ghaw.net",
        "dns/fileshare.ghaw.net", "dns/factions.ghaw.net", "dns/rumor.ghaw.net"])
    assert links(res) == sorted([
        ["routes_to", "dns/ghaw.net", "tunnel/" + FOUNDRY],
        ["routes_to", "dns/foundry.ghaw.net", "tunnel/" + FOUNDRY],
        ["routes_to", "dns/proxmox.ghaw.net", "tunnel/" + FOUNDRY],
        ["routes_to", "dns/ssh.ghaw.net", "tunnel/" + FOUNDRY],
        ["routes_to", "dns/fileshare.ghaw.net", "tunnel/" + FILESHARE],
        ["routes_to", "dns/factions.ghaw.net", "fqdn:ghaw.net"],
        ["routes_to", "dns/rumor.ghaw.net", "fqdn:ghaw.net"],
    ])
    t = next(o for o in res.obs if o.ext_id == "tunnel/" + FOUNDRY)
    assert t.name == "foundry" and t.attrs["config"] == "local" and t.attrs["connections"] == 2
    assert "cftunnel:" + FOUNDRY in t.keys
    assert res.warnings == []


def test_mail_records_skipped():
    res = run()
    assert not any(o.attrs.get("type") in ("MX", "TXT") for o in res.obs)


def test_bearer_token_sent():
    seen = []
    run(seen=seen)
    assert seen[0].headers["authorization"] == "Bearer tok"


def test_pagination_follows_all_pages():
    assert len(run(page_size=2).obs) == len(run().obs)


def test_zone_filter():
    res = run(cfg_extra={"zones": ["other.net"]})
    assert res.obs == [] and res.links == []


def test_missing_tunnel_permission_is_a_warning():
    res = run(overrides={"/accounts/acct-1/cfd_tunnel": 403})
    assert any("Cloudflare Tunnel" in w for w in res.warnings)
    # dns still works, tunnel targets fall back to keys
    assert ["routes_to", "dns/ghaw.net", "cftunnel:" + FOUNDRY] in links(res)


def test_bad_token_raises():
    with pytest.raises(PermissionError):
        run(overrides={"/zones": 401})


def test_remote_tunnel_ingress():
    t = {"id": "rem-1", "name": "dash", "status": "healthy", "remote_config": True, "connections": []}
    res = run(overrides={
        "/accounts/acct-1/cfd_tunnel": [t],
        "/zones/zone-ghaw/dns_records": [{"type": "CNAME", "name": "vtt.ghaw.net", "content": "rem-1.cfargotunnel.com"}],
        "/accounts/acct-1/cfd_tunnel/rem-1/configurations": {"config": {"ingress": [
            {"hostname": "vtt.ghaw.net", "service": "http://192.168.1.69:30000"},
            {"hostname": "pve.ghaw.net", "service": "https://localhost:8006"},
            {"service": "http_status:404"}]}},
    })
    assert ["routes_to", "tunnel/rem-1", "ip:192.168.1.69:30000"] in links(res)
    assert any("local to cloudflared" in w for w in res.warnings)


def test_a_record_links_to_ip():
    res = run(overrides={"/accounts/acct-1/cfd_tunnel": [],
                         "/zones/zone-ghaw/dns_records": [{"type": "A", "name": "nas.ghaw.net", "content": "10.0.0.5"},
                                                          {"type": "A", "name": "nas.ghaw.net", "content": "10.0.0.6"}]})
    assert [o.ext_id for o in res.obs] == ["dns/nas.ghaw.net"]
    assert links(res) == [["routes_to", "dns/nas.ghaw.net", "ip:10.0.0.5"],
                          ["routes_to", "dns/nas.ghaw.net", "ip:10.0.0.6"]]


@pytest.mark.parametrize("svc,key", [
    ("http://192.168.1.69:30000", "ip:192.168.1.69:30000"),
    ("http://192.168.1.70:80", "ip:192.168.1.70:80"),
    ("http://192.168.1.70", "ip:192.168.1.70:80"),
    ("https://localhost:8006", "local:8006"),
    ("ssh://localhost:22", "local:22"),
    ("http://foundry.lan:30000", "dns:foundry.lan:30000"),
    ("http_status:404", None),
    ("hello_world", None),
])
def test_service_key(svc, key):
    assert service_key(svc) == key
