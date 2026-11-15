"""Cloudflare source: DNS records + tunnels via the API (read-only token)."""
import ipaddress
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, SecretStr

from sporemap.sources.base import Link, Obs, Result, Source

API = "https://api.cloudflare.com/client/v4"
ROUTED = ("A", "AAAA", "CNAME")  # records that point traffic somewhere. MX/TXT etc are skipped
TUNNEL_SUFFIX = ".cfargotunnel.com"


class CloudflareConfig(BaseModel):
    api_token: SecretStr
    zones: list[str] = []  # only these zone names, empty = every zone the token can see
    timeout: float = 15


def service_key(svc):
    # cloudflared ingress service -> correlation key, or None if it points nowhere useful
    # "http://192.168.1.69:30000" -> "ip:192.168.1.69:30000", "ssh://localhost:22" -> "local:22"
    if not svc or svc.startswith(("http_status:", "hello_world", "bastion", "socks5")):
        return None
    u = urlsplit(svc if "://" in svc else "tcp://" + svc)
    h, p = u.hostname, u.port
    if not h:
        return None
    if p is None:
        p = {"http": 80, "https": 443, "ssh": 22, "rdp": 3389}.get(u.scheme)
    tail = ":" + str(p) if p else ""
    if h in ("localhost", "127.0.0.1", "::1"):
        return "local" + tail  # relative to wherever cloudflared runs, caller decides
    try:
        ipaddress.ip_address(h)
        return "ip:" + h + tail
    except ValueError:
        return "dns:" + h + tail


class Cloudflare(Source):
    """Cloudflare: DNS records and tunnels, so public hostnames link into your lab."""

    name = "cloudflare"
    Config = CloudflareConfig
    transport = None  # tests swap in a MockTransport

    def _client(self, cfg):
        return httpx2.AsyncClient(base_url=API, timeout=cfg.timeout, transport=self.transport,
                                  headers={"Authorization": "Bearer " + cfg.api_token.get_secret_value()})

    async def _get(self, c, path, **params):
        r = await c.get(path, params=params)
        if r.status_code in (401, 403):
            raise PermissionError("cloudflare said " + str(r.status_code) + " for " + path + ", check the token's permissions")
        r.raise_for_status()
        j = r.json()
        if not j.get("success", True):
            raise RuntimeError("cloudflare error on " + path + ": " + str(j.get("errors")))
        return j

    async def _all(self, c, path, **params):
        # follow page/total_pages until done
        out, page = [], 1
        while True:
            j = await self._get(c, path, page=page, per_page=100, **params)
            out += j.get("result") or []
            info = j.get("result_info") or {}
            if page >= (info.get("total_pages") or 1):
                return out
            page += 1

    async def test(self, cfg):
        async with self._client(cfg) as c:
            await self._get(c, "/user/tokens/verify")

    async def discover(self, cfg):
        res = Result()
        async with self._client(cfg) as c:
            zones = await self._all(c, "/zones")
            if cfg.zones:
                zones = [z for z in zones if z["name"] in cfg.zones]
            accounts = sorted({(z.get("account") or {}).get("id") for z in zones} - {None})

            tunnels = {}
            for a in accounts:
                try:
                    for t in await self._all(c, "/accounts/" + a + "/cfd_tunnel", is_deleted="false"):
                        tunnels[t["id"]] = (a, t)
                except PermissionError:
                    res.warnings.append("token cant read tunnels on account " + a + " (needs Cloudflare Tunnel: Read)")

            for tid, (acct, t) in sorted(tunnels.items(), key=lambda kv: kv[1][1].get("name", "")):
                remote = t.get("remote_config") or t.get("config_src") == "cloudflare"
                eid = "tunnel/" + tid
                res.obs.append(Obs("external", eid, t.get("name") or tid, {
                    "provider": "cloudflare", "type": "tunnel", "status": t.get("status"),
                    "config": "remote" if remote else "local", "connections": len(t.get("connections") or [])},
                    ["cftunnel:" + tid]))
                if remote:
                    await self._remote_ingress(c, acct, tid, eid, res)

            seen = set()
            for z in zones:
                for rec in await self._all(c, "/zones/" + z["id"] + "/dns_records"):
                    if rec.get("type") not in ROUTED:
                        continue
                    name, val = rec["name"], rec.get("content", "")
                    eid = "dns/" + name
                    if eid not in seen:  # round robin A records share a name, one node is enough
                        seen.add(eid)
                        res.obs.append(Obs("endpoint", eid, name, {
                            "zone": z["name"], "type": rec["type"], "content": val,
                            "proxied": bool(rec.get("proxied"))}, ["fqdn:" + name]))
                    if rec["type"] == "CNAME" and val.endswith(TUNNEL_SUFFIX):
                        tid = val[: -len(TUNNEL_SUFFIX)]
                        dst = "tunnel/" + tid if tid in tunnels else "cftunnel:" + tid
                        res.links.append(Link("routes_to", eid, dst))
                    elif rec["type"] == "CNAME":
                        res.links.append(Link("routes_to", eid, "fqdn:" + val.rstrip(".")))
                    else:
                        res.links.append(Link("routes_to", eid, "ip:" + val))
        res.links = list({(ln.kind, ln.src, ln.dst): ln for ln in res.links}.values())
        return res.check()

    async def _remote_ingress(self, c, acct, tid, eid, res):
        # dashboard-managed tunnels keep their routes in the api, local ones only on disk
        try:
            j = await self._get(c, "/accounts/" + acct + "/cfd_tunnel/" + tid + "/configurations")
        except (PermissionError, httpx2.HTTPError) as e:
            res.warnings.append("couldnt read ingress for tunnel " + tid + ": " + str(e))
            return
        for rule in (((j.get("result") or {}).get("config") or {}).get("ingress")) or []:
            k = service_key(rule.get("service"))
            if k and not k.startswith("local"):
                res.links.append(Link("routes_to", eid, k))
            elif k:
                res.warnings.append("tunnel " + tid + " routes " + str(rule.get("hostname")) + " to " + rule["service"]
                                    + ", which is local to cloudflared and cant be placed from the api")
