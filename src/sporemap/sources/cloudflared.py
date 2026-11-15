"""cloudflared source: reads a locally managed tunnel's config.yml over SSH.

Meant for a locked-down key whose authorized_keys line forces
command="cat /etc/cloudflared/config.yml", so the key can do nothing else.
"""
import ipaddress
import os
import re

import asyncssh
import yaml
from pydantic import BaseModel

from sporemap.sources.base import Link, Obs, Result, Source
from sporemap.sources.cloudflare import service_key

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CloudflaredConfig(BaseModel):
    host: str  # box running cloudflared
    port: int = 22
    user: str = "sporemap"
    key_path: str  # private key file, eg ~/.ssh/sporemap
    path: str = "/etc/cloudflared/config.yml"  # ignored when the key has a forced command, which is the point
    known_hosts: str | None = None  # path to known_hosts, None = your normal ~/.ssh/known_hosts
    host_ip: str | None = None  # lan ip of that box, for localhost services. defaults to host if its an ip
    timeout: float = 15


def _ip_or_none(s):
    try:
        ipaddress.ip_address(s)
        return s
    except ValueError:
        return None


def parse(text, host_ip):
    conf = yaml.safe_load(text) or {}
    res = Result()
    tun = str(conf.get("tunnel") or "").strip()
    if not tun:
        raise ValueError("config has no tunnel: line")

    keys = ["cftunnel:" + tun] if UUID.match(tun) else []
    tid = "tunnel/" + tun
    routes = {}
    targets = []
    for rule in conf.get("ingress") or []:
        k = service_key(rule.get("service"))
        if k and k.startswith("local"):
            if not host_ip:
                res.warnings.append(str(rule.get("hostname")) + " -> " + rule["service"] + " is local, set host_ip to place it")
                k = None
            else:
                k = "ip:" + host_ip + k[len("local"):]
        h = rule.get("hostname")
        if h:
            routes[h] = rule.get("service")
            eid = "host/" + h
            if eid not in {o.ext_id for o in res.obs}:
                res.obs.append(Obs("endpoint", eid, h, {}, ["fqdn:" + h]))
                res.links.append(Link("routes_to", eid, tid))
        if k and k not in targets:
            targets.append(k)

    res.obs.insert(0, Obs("external", tid, tun, {"provider": "cloudflare", "type": "tunnel",
                                                  "config": "local", "routes": routes}, keys))
    for k in targets:
        res.links.append(Link("routes_to", tid, k))
    if host_ip:
        res.links.append(Link("runs", "ip:" + host_ip, tid))  # the connector daemon lives on this box
    return res.check()


class Cloudflared(Source):
    """cloudflared: a locally managed tunnel's routes, read from config.yml over SSH."""

    name = "cloudflared"
    Config = CloudflaredConfig

    async def _fetch(self, cfg):
        kh = os.path.expanduser(cfg.known_hosts) if cfg.known_hosts else ()  # () = asyncssh default file
        async with asyncssh.connect(cfg.host, port=cfg.port, username=cfg.user, client_keys=[os.path.expanduser(cfg.key_path)],
                                    known_hosts=kh, connect_timeout=cfg.timeout) as conn:
            r = await conn.run("cat " + cfg.path, check=True, timeout=cfg.timeout)
            return r.stdout

    async def test(self, cfg):
        parse(await self._fetch(cfg), None)

    async def discover(self, cfg):
        text = await self._fetch(cfg)
        # print(text)
        return parse(text, cfg.host_ip or _ip_or_none(cfg.host))
