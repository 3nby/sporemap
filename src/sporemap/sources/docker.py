"""Docker source: containers and their published ports, via a read-only socket proxy.

The proxy should only listen on 127.0.0.1 of the docker box. Set ssh: on a host and
sporemap reaches it through an ssh port forward (optionally hopping through a jump
box, for machines on internal bridges), so the proxy never touches the network.
"""
import ipaddress
import os
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlsplit

import asyncssh
import httpx2
from pydantic import BaseModel

from sporemap.sources.base import Link, Obs, Result, Source

LOOPBACK = ("127.0.0.1", "::1")


class DockerHost(BaseModel):
    name: str  # just a label, like "fileshare"
    url: str = "http://127.0.0.1:2375"  # a read-only socket proxy, never the raw docker socket over tcp
    # with ssh set, url is where the proxy listens ON the docker box (so usually leave it alone)
    host_ip: str | None = None  # ip of the box docker runs on. links containers to it + places published ports
    ssh: str | None = None  # [user@]host[:port] of the docker box, turns on tunnel mode
    jump: str | None = None  # [user@]host[:port] to hop through first, eg the proxmox host
    key_path: str | None = None  # overrides the one on the docker: section
    known_hosts: str | None = None


class DockerConfig(BaseModel):
    hosts: list[DockerHost]
    include_stopped: bool = True
    timeout: float = 10
    key_path: str | None = None  # default ssh key for every host, eg ~/.ssh/sporemap
    known_hosts: str | None = None  # None = your normal ~/.ssh/known_hosts
    ssh_user: str = "sporemap"


def _ip_or_none(s):
    try:
        ipaddress.ip_address(s or "")
        return s
    except ValueError:
        return None


def _target(s, user):
    # "sporemap@10.0.0.5:2222" -> ("sporemap", "10.0.0.5", 2222). v6 needs [brackets] with a port
    u, _, hp = s.rpartition("@")
    port = 22
    if hp.startswith("["):
        h, _, rest = hp[1:].partition("]")
        if rest.startswith(":"):
            port = int(rest[1:])
    elif hp.count(":") == 1:
        h, p = hp.split(":")
        port = int(p)
    else:
        h = hp
    return (u or user), h, port


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

    @asynccontextmanager
    async def _base(self, cfg, h):
        """Yields the base url to talk to. Plain url, or a local port forwarded over ssh."""
        if not h.ssh:
            yield h.url
            return
        key = h.key_path or cfg.key_path
        if not key:
            raise ValueError(h.name + ": ssh is set but no key_path (set it on the host or the docker: section)")
        kh = h.known_hosts or cfg.known_hosts
        kw = {"client_keys": [os.path.expanduser(key)], "connect_timeout": cfg.timeout,
              "known_hosts": os.path.expanduser(kh) if kh else ()}  # () = asyncssh default file
        tgt = urlsplit(h.url)
        async with AsyncExitStack() as st:
            if h.jump:
                ju, jh, jp = _target(h.jump, cfg.ssh_user)
                kw["tunnel"] = await st.enter_async_context(asyncssh.connect(jh, port=jp, username=ju, **kw))
            u, host, port = _target(h.ssh, cfg.ssh_user)
            conn = await st.enter_async_context(asyncssh.connect(host, port=port, username=u, **kw))
            # port 0 = let the os pick a free local port
            lis = await conn.forward_local_port("127.0.0.1", 0, tgt.hostname or "127.0.0.1", tgt.port or 2375)
            st.callback(lis.close)
            # print(h.name, "forwarded to local port", lis.get_port())
            yield "http://127.0.0.1:" + str(lis.get_port())

    def _host_ip(self, h):
        if h.host_ip:
            return h.host_ip
        if h.ssh:
            return _ip_or_none(_target(h.ssh, "")[1])  # the box we ssh into is the docker box
        ip = _ip_or_none(urlsplit(h.url).hostname)
        return None if ip in LOOPBACK else ip  # 127.0.0.1 means "via a tunnel", not where docker actually is

    async def test(self, cfg):
        for h in cfg.hosts:
            async with self._base(cfg, h) as base, self._client(cfg, base) as c:
                await self._get(c, "/info")

    async def discover(self, cfg):
        res = Result()
        ok = 0
        for h in cfg.hosts:
            try:
                async with self._base(cfg, h) as base:
                    await self._one(cfg, h, base, res)
                ok += 1
            except (httpx2.HTTPError, OSError, asyncssh.Error, ValueError) as e:  # OSError covers PermissionError + refused conns
                res.warnings.append(h.name + ": " + type(e).__name__ + ": " + str(e))
        if cfg.hosts and not ok:
            raise RuntimeError("every docker host failed: " + "; ".join(res.warnings))
        return res.check()

    async def _one(self, cfg, h, base, res):
        # base is the plain url or the forwarded local port from _base
        host_ip = self._host_ip(h)
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
