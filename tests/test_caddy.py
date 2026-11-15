import asyncio
import json
from pathlib import Path

import httpx2
import pytest

from sporemap.sources.caddy import Caddy, CaddyConfig, _split, walk

FIX = Path(__file__).parent / "fixtures" / "caddy"


def run(conf, **kw):
    c = Caddy()
    c.transport = httpx2.MockTransport(lambda req: httpx2.Response(200, json=conf))
    return asyncio.run(c.discover(CaddyConfig(url="http://caddy.test:2020", **kw)))


def links(res):
    return sorted([ln.kind, ln.src, ln.dst] for ln in res.links)


def test_real_config_ghostprox():
    res = run(json.loads((FIX / "ghostprox.json").read_text()), host_ip="192.168.1.70")
    assert sorted(o.ext_id for o in res.obs) == ["app/caddy", "host/ghaw.net", "host/www.ghaw.net"]
    assert links(res) == [
        ["routes_to", "app/caddy", "ip:192.168.1.71:8080"],
        ["routes_to", "host/ghaw.net", "app/caddy"],
        ["routes_to", "host/www.ghaw.net", "app/caddy"],
        ["runs", "ip:192.168.1.70", "app/caddy"],
    ]
    app = next(o for o in res.obs if o.ext_id == "app/caddy")
    assert app.attrs["sites"] == {"ghaw.net": ["ip:192.168.1.71:8080"], "www.ghaw.net": ["ip:192.168.1.71:8080"]}
    assert sorted(app.keys) == ["ip:192.168.1.70:2020", "ip:192.168.1.70:80"]
    assert res.warnings == []  # the admin proxy on :2020 is skipped quietly


def test_no_host_ip_means_no_runs_link():
    res = run(json.loads((FIX / "ghostprox.json").read_text()))
    assert not any(ln.kind == "runs" for ln in res.links)


def _srv(routes, listen=(":80",)):
    return {"apps": {"http": {"servers": {"s": {"listen": list(listen), "routes": routes}}}}}


def _rp(*dials):
    return {"handler": "reverse_proxy", "upstreams": [{"dial": d} for d in dials]}


def test_localhost_upstream_uses_host_ip():
    res = run(_srv([{"match": [{"host": ["app.lan"]}], "handle": [_rp("localhost:3000")]}]), host_ip="10.0.0.5")
    assert ["routes_to", "app/caddy", "ip:10.0.0.5:3000"] in links(res)


def test_localhost_without_host_ip_warns():
    res = run(_srv([{"match": [{"host": ["app.lan"]}], "handle": [_rp("127.0.0.1:3000")]}]))
    assert any("set host_ip" in w for w in res.warnings)
    assert [o.ext_id for o in res.obs] == ["app/caddy"]  # site with no usable upstream is dropped


def test_hostname_upstream_becomes_dns_key():
    res = run(_srv([{"match": [{"host": ["vtt.lan"]}], "handle": [_rp("foundry:30000")]}]))
    assert ["routes_to", "app/caddy", "dns:foundry:30000"] in links(res)


def test_dynamic_upstream_skipped():
    res = run(_srv([{"match": [{"host": ["x.lan"]}], "handle": [_rp("{http.request.host}:80")]}]))
    assert any("dynamic" in w for w in res.warnings)


def test_catch_all_site_has_no_endpoint():
    res = run(_srv([{"handle": [_rp("10.0.0.9:80")]}], listen=[":8443"]))
    assert [o.kind for o in res.obs] == ["app"]
    assert ["routes_to", "app/caddy", "ip:10.0.0.9:80"] in links(res)


def test_walk_carries_hosts_into_subroutes():
    out = walk([{"match": [{"host": ["a.lan", "b.lan"]}],
                 "handle": [{"handler": "subroute", "routes": [{"handle": [_rp("10.0.0.1:1")]}]}]}], {"*"}, {})
    assert out == {"a.lan": {"10.0.0.1:1"}, "b.lan": {"10.0.0.1:1"}}


def test_split():
    assert _split("192.168.1.71:8080") == ("192.168.1.71", "8080")
    assert _split("[::1]:80") == ("::1", "80")
    assert _split("foundry:30000") == ("foundry", "30000")


def test_http_error_raises():
    c = Caddy()
    c.transport = httpx2.MockTransport(lambda req: httpx2.Response(403))
    with pytest.raises(httpx2.HTTPStatusError):
        asyncio.run(c.discover(CaddyConfig()))
