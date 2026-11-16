"""Docker source: containers and their published ports, via a read-only socket proxy."""
import ipaddress
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel

from sporemap.sources.base import Link, Obs, Result, Source

LOOPBACK = ("127.0.0.1", "::1")


class DockerHost(BaseModel):
    name: str  # just a label, like "fileshare"
    url: str = "http://127.0.0.1:2375"  # a read-only socket proxy, never the raw docker socket over tcp
    host_ip: str | None = None  # ip of the box docker runs on. links containers to it + places published ports


class DockerConfig(BaseModel):
    hosts: list[DockerHost]
    include_stopped: bool = True
    timeout: float = 10


def _ip_or_none(s):
    try:
        ipaddress.ip_address(s or "")
        return s
    except ValueError:
        return None


def _name(c):
    n = (c.get("Names") or ["/" + c["Id"][:12]])[0]
    return n.lstrip("/")


class Docker(Source):
    """Docker: containers, images and published ports (read-only socket proxy)."""

    name = "docker"
    Config = DockerConfig
    transport = None  # tests swap in a MockTransport

    def _client(self, cfg, base=None):
        return httpx2.AsyncClient(base_url=base or cfg.url, timeout=cfg.timeout, transport=self.transport)

    async def _get(self, c, path, **params):
        r = await c.get(path, params=params)
        if r.status_code == 403:
            raise PermissionError("docker proxy refused " + path + ", enable it on the proxy (CONTAINERS=1, INFO=1)")
        r.raise_for_status()
        return r.json()

    async def test(self, cfg):
        for h in cfg.hosts:
            async with self._client(cfg, h.url) as c:
                await self._get(c, "/info")

    async def discover(self, cfg):
        res = Result()
        ok = 0
        for h in cfg.hosts:
            try:
                await self._one(cfg, h, h.url, res)
                ok += 1
            except (httpx2.HTTPError, PermissionError) as e:
                res.warnings.append(h.name + ": " + type(e).__name__ + ": " + str(e))
        if cfg.hosts and not ok:
            raise RuntimeError("every docker host failed: " + "; ".join(res.warnings))
        return res.check()

    async def _one(self, cfg, h, base, res):
        # base is split out so the ssh tunnel mode can point at its forwarded port
        host_ip = h.host_ip or _ip_or_none(urlsplit(h.url).hostname)
        if host_ip in LOOPBACK:
            host_ip = None  # 127.0.0.1 means "via a tunnel", not where docker actually is
        if not host_ip:
            res.warnings.append(h.name + ": host_ip not set, containers wont be linked to the machine running them")

        async with self._client(cfg, base) as c:
            info = await self._get(c, "/info")
            ctrs = await self._get(c, "/containers/json", all=str(cfg.include_stopped).lower())

        engine = info.get("ID") or info.get("Name") or "docker"
        for ct in sorted(ctrs, key=_name):
            cid = ct["Id"]
            eid = "ctr/" + cid  # full id, short ids can collide
            lab = ct.get("Labels") or {}
            ports, keys = [], ["docker:" + engine + "/" + cid]
            for p in ct.get("Ports") or []:
                pub = p.get("PublicPort")
                if not pub:
                    continue  # exposed but not published, unreachable from outside
                bind = p.get("IP") or "0.0.0.0"
                ports.append({"host_port": pub, "port": p.get("PrivatePort"), "proto": p.get("Type", "tcp"),
                              "bind": bind})
                if host_ip and bind not in LOOPBACK:
                    keys.append("ip:" + host_ip + ":" + str(pub))
            # docker lists v4 + v6 binds separately, dedupe
            ports = [dict(t) for t in dict.fromkeys(tuple(sorted(d.items())) for d in ports
                                                     if d["bind"] not in ("::", "[::]"))]
            attrs = {
                "image": ct.get("Image"), "state": ct.get("State"), "status": ct.get("Status"),
                "ports": ports, "networks": sorted((ct.get("NetworkSettings") or {}).get("Networks") or {}),
                "compose_project": lab.get("com.docker.compose.project"),
                "compose_service": lab.get("com.docker.compose.service"), "engine": info.get("Name"),
            }
            res.obs.append(Obs("container", eid, _name(ct), attrs, list(dict.fromkeys(keys))))
            attrs["docker_host"] = h.name
            if host_ip:
                res.links.append(Link("hosts", "ip:" + host_ip, eid))
        # print(h.name, len(ctrs), "containers")
