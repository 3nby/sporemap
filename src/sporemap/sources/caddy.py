"""Caddy source: reads the live JSON config from the admin API (or a read-only proxy of it)."""
import ipaddress

import httpx2
from pydantic import BaseModel

from sporemap.sources.base import Link, Obs, Result, Source

LOCAL = ("localhost", "127.0.0.1", "::1", "[::1]")


class CaddyConfig(BaseModel):
    url: str = "http://127.0.0.1:2019"  # admin api, or the read-only :2020 proxy
    host_ip: str | None = None  # ip of the box caddy runs on. links it to its CT + fixes localhost upstreams
    admin_port: int = 2019  # upstreams pointing here are caddy talking to itself, skip them
    timeout: float = 10


def _split(dial):
    # "192.168.1.71:8080" -> ("192.168.1.71", "8080"), "[::1]:80" -> ("::1", "80")
    if dial.startswith("["):
        h, _, p = dial[1:].partition("]:")
        return h, p
    h, _, p = dial.rpartition(":")
    return (h, p) if h else (dial, "")


def _is_ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def walk(routes, hosts, out):
    # collect {host: set(dials)}. caddyfile nests host matchers outside subroutes, so carry hosts down
    for r in routes or []:
        hs = set()
        for m in r.get("match") or []:
            hs.update(m.get("host") or [])
        here = hs or hosts
        for h in r.get("handle") or []:
            if h.get("handler") == "reverse_proxy":
                for u in h.get("upstreams") or []:
                    if u.get("dial"):
                        for site in here:
                            out.setdefault(site, set()).add(u["dial"])
            elif h.get("handler") == "subroute":
                walk(h.get("routes"), here, out)
    return out


class Caddy(Source):
    """Caddy: hostnames it serves and where it proxies them (admin API, read-only)."""

    name = "caddy"
    Config = CaddyConfig
    transport = None  # tests swap in a MockTransport

    async def _config(self, cfg):
        async with httpx2.AsyncClient(timeout=cfg.timeout, transport=self.transport) as c:
            r = await c.get(cfg.url.rstrip("/") + "/config/")
            r.raise_for_status()
            return r.json() or {}

    async def test(self, cfg):
        await self._config(cfg)

    async def discover(self, cfg):
        conf = await self._config(cfg)
        res = Result()
        servers = (((conf.get("apps") or {}).get("http") or {}).get("servers")) or {}

        sites, listen = {}, []
        for sname in sorted(servers):
            srv = servers[sname]
            listen += srv.get("listen") or []
            # routes with no host matcher are catch-alls, name them after their listen address
            anon = {"*" + a for a in srv.get("listen") or []} or {"*"}
            walk(srv.get("routes"), anon, sites)

        ports = sorted({a.rsplit(":", 1)[-1] for a in listen if ":" in a})
        keys = ["ip:" + cfg.host_ip + ":" + p for p in ports] if cfg.host_ip else []
        app = Obs("app", "app/caddy", "Caddy", {"listen": listen}, keys)
        res.obs.append(app)
        if cfg.host_ip:
            res.links.append(Link("runs", "ip:" + cfg.host_ip, "app/caddy"))

        shown, targets = {}, set()
        for site in sorted(sites):
            dials = []
            for d in sorted(sites[site]):
                h, p = _split(d)
                if "{" in d:  # placeholders like {http.request.host}, cant resolve statically
                    res.warnings.append("skipping dynamic upstream " + d)
                    continue
                if h in LOCAL:
                    if p == str(cfg.admin_port):
                        continue  # thats caddy proxying its own admin api
                    if not cfg.host_ip:
                        res.warnings.append(site + " -> " + d + " is local to caddy, set host_ip to place it")
                        continue
                    h = cfg.host_ip
                key = ("ip:" if _is_ip(h) else "dns:") + h + (":" + p if p else "")
                dials.append(key)
                targets.add(key)
            if not dials:
                continue
            shown[site] = dials
            if not site.startswith("*"):
                eid = "host/" + site
                res.obs.append(Obs("endpoint", eid, site, {}, ["fqdn:" + site]))
                res.links.append(Link("routes_to", eid, "app/caddy"))

        app.attrs["sites"] = shown
        for t in sorted(targets):
            res.links.append(Link("routes_to", "app/caddy", t))
        # print(shown)
        return res.check()
