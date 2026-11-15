"""Proxmox VE source. Read-only: needs an API token with the PVEAuditor role."""
import asyncio
import re

import httpx2
from pydantic import BaseModel, SecretStr

from sporemap.sources.base import Link, Obs, Result, Source

MAC = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")
# interfaces inside a guest that are local plumbing, not real addresses
# (docker bridges reuse 172.17.0.1 in every CT, which would wreck ip matching)
VIRT_IF = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "podman", "lxcbr", "vnet")
DISK = re.compile(r"^(rootfs|mp\d+|scsi\d+|virtio\d+|sata\d+|ide\d+|efidisk\d+|tpmstate\d+)$")


class ProxmoxConfig(BaseModel):
    host: str
    port: int = 8006
    token_id: str  # like sporemap@pve!scan
    token_secret: SecretStr
    verify_ssl: bool = True  # most homelabs use the self-signed cert, so set false there
    timeout: float = 15


def kv(s):
    # "name=eth0,bridge=vmbr2,ip=dhcp" -> dict. bare values (like "local-lvm:vm-1-disk-0") land under ""
    out = {}
    for part in s.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        elif part:
            out.setdefault("", part.strip())
    return out


def nic_info(raw):
    # works for both lxc ("hwaddr=..,bridge=..,ip=..") and qemu ("virtio=MAC,bridge=..")
    d = kv(raw)
    mac = d.get("hwaddr")
    if not mac:
        mac = next((v for v in d.values() if MAC.match(v)), None)
    ip = d.get("ip", "")
    ip = ip.split("/")[0] if ip and ip not in ("dhcp", "manual") else None
    return {"mac": mac.lower() if mac else None, "bridge": d.get("bridge"), "ip": ip}


def disk_storage(raw):
    # "local-lvm:vm-202-disk-0,size=4G" -> "local-lvm". bind mounts + cdroms -> None
    d = kv(raw)
    if d.get("media") == "cdrom":
        return None
    vol = d.get("volume") or d.get("")
    if not vol or ":" not in vol or vol.startswith("/"):
        return None
    st = vol.split(":")[0]
    return None if st == "none" else st


def _agent_on(cfg):
    # agent can be "1", "0", or "enabled=1,fstrim_cloned_disks=1"
    a = kv(str(cfg.get("agent", "0")))
    return a.get("enabled", a.get("")) == "1"


class Proxmox(Source):
    """Proxmox VE: nodes, VMs, CTs, bridges and storage (read-only API token)."""

    name = "proxmox"
    Config = ProxmoxConfig
    transport = None  # tests swap in a MockTransport

    def _client(self, cfg):
        auth = "PVEAPIToken=" + cfg.token_id + "=" + cfg.token_secret.get_secret_value()
        return httpx2.AsyncClient(
            base_url="https://" + cfg.host + ":" + str(cfg.port) + "/api2/json",
            headers={"Authorization": auth}, verify=cfg.verify_ssl, timeout=cfg.timeout,
            transport=self.transport)

    async def _get(self, c, path):
        r = await c.get(path)
        if r.status_code == 401:
            raise PermissionError("proxmox rejected the API token (401), check token_id and the secret")
        r.raise_for_status()
        return r.json()["data"]

    async def test(self, cfg):
        async with self._client(cfg) as c:
            await self._get(c, "/version")

    async def discover(self, cfg):
        res = Result()
        async with self._client(cfg) as c:
            status = await self._get(c, "/cluster/status")
            items = await self._get(c, "/cluster/resources")

            nodes = [x for x in items if x.get("type") == "node"]
            cl = next((x["name"] for x in status if x.get("type") == "cluster"), None)
            cl = cl or (nodes[0]["node"] if len(nodes) == 1 else "pve")  # standalone node = its own "cluster"
            node_ip = {x["name"]: x.get("ip") for x in status if x.get("type") == "node"}
            online = {x["node"] for x in nodes if x.get("status") == "online"}
            pre = "pve:" + cl + "/"

            # bridges first so guests can link to them
            bridges = {}
            for n in sorted(online):
                try:
                    nets = await self._get(c, "/nodes/" + n + "/network")
                except httpx2.HTTPError as e:
                    res.warnings.append("network list for " + n + " failed: " + repr(e))
                    nets = []
                bridges[n] = [x for x in nets if x.get("type") in ("bridge", "OVSBridge")]

            for x in sorted(nodes, key=lambda y: y["node"]):
                n = x["node"]
                keys = [pre + "node/" + n]
                ips = [i for i in [node_ip.get(n)] + [b.get("address") for b in bridges.get(n, [])] if i]
                keys += ["ip:" + i for i in dict.fromkeys(ips)]
                res.obs.append(Obs("host", "node/" + n, n, {
                    "status": x.get("status"), "cpus": x.get("maxcpu"), "mem": x.get("maxmem"),
                    "ip": node_ip.get(n), "platform": "proxmox"}, keys))
                for b in bridges.get(n, []):
                    bid = "net/" + n + "/" + b["iface"]
                    res.obs.append(Obs("network", bid, b["iface"], {
                        "cidr": b.get("cidr"), "address": b.get("address"),
                        "ports": b.get("bridge_ports"), "active": bool(b.get("active"))},
                        [pre + bid]))
                    if b.get("address"):
                        res.links.append(Link("member_of", "node/" + n, bid))

            stor = set()
            for x in items:
                if x.get("type") != "storage":
                    continue
                sid = "storage/" + x["node"] + "/" + x["storage"]
                stor.add(sid)
                res.obs.append(Obs("storage", sid, x["storage"], {
                    "type": x.get("plugintype"), "content": x.get("content"),
                    "shared": bool(x.get("shared")), "size": x.get("maxdisk"), "node": x["node"]}))

            guests = [x for x in items if x.get("type") in ("lxc", "qemu") and not x.get("template")]
            sem = asyncio.Semaphore(8)  # dont hammer the api

            async def one(g):
                async with sem:
                    return await self._guest(c, g, pre, online, bridges, stor, res)

            for o, links in await asyncio.gather(*(one(g) for g in guests)):
                res.obs.append(o)
                res.links += links
        return res.check()

    async def _guest(self, c, g, pre, online, bridges, stor, res):
        t, vmid, n = g["type"], g["vmid"], g["node"]
        gid = t + "/" + str(vmid)
        base = "/nodes/" + n + "/" + t + "/" + str(vmid)
        attrs = {"vmid": vmid, "type": t, "status": g.get("status"), "node": n,
                 "cpus": g.get("maxcpu"), "mem": g.get("maxmem"), "disk": g.get("maxdisk")}
        keys = [pre + str(vmid)]
        links = [Link("hosts", "node/" + n, gid)]

        cfg = {}
        if n in online:
            try:
                cfg = await self._get(c, base + "/config")
            except httpx2.HTTPError as e:
                res.warnings.append(gid + " config failed: " + repr(e))

        known_br = {b["iface"] for b in bridges.get(n, [])}
        nics, ips = [], []
        for k in sorted(cfg):
            if re.fullmatch(r"net\d+", k):
                ni = nic_info(cfg[k])
                nics.append(dict(ni, dev=k))
                if ni["mac"]:
                    keys.append("mac:" + ni["mac"])
                if ni["ip"]:
                    ips.append(ni["ip"])
                if ni["bridge"] in known_br:
                    links.append(Link("member_of", gid, "net/" + n + "/" + ni["bridge"]))
            elif DISK.match(k):
                st = disk_storage(cfg[k])
                sid = "storage/" + n + "/" + st if st else None
                if sid in stor:
                    links.append(Link("mounts", gid, sid))

        # live ips beat static config, but only for running guests
        if g.get("status") == "running":
            ips += await self._live_ips(c, base, t, cfg, gid, res)

        ips = list(dict.fromkeys(i for i in ips if i and not i.startswith(("127.", "fe80"))))
        keys += ["ip:" + i for i in ips]
        attrs["ips"] = ips
        attrs["nics"] = nics
        # dedupe links (two nics on one bridge) + keys
        links = list({(ln.kind, ln.dst): ln for ln in links}.values())
        return Obs("workload", gid, g.get("name") or gid, attrs, list(dict.fromkeys(keys))), links

    async def _live_ips(self, c, base, t, cfg, gid, res):
        out = []
        try:
            if t == "lxc":
                for i in await self._get(c, base + "/interfaces") or []:
                    if i.get("inet") and not str(i.get("name", "")).startswith(VIRT_IF):
                        out.append(i["inet"].split("/")[0])
            elif _agent_on(cfg):
                d = await self._get(c, base + "/agent/network-get-interfaces")
                for i in (d or {}).get("result", []):
                    if str(i.get("name", "")).startswith(VIRT_IF):
                        continue
                    for a in i.get("ip-addresses", []):
                        if a.get("ip-address-type") == "ipv4":
                            out.append(a["ip-address"])
        except httpx2.HTTPStatusError as e:
            if e.response.status_code == 403:
                res.warnings.append(gid + ": no permission for live IPs (needs VM.GuestAgent.Audit on PVE 9)")
            else:
                res.warnings.append(gid + ": live IPs failed: " + str(e.response.status_code))
        except httpx2.HTTPError as e:
            res.warnings.append(gid + ": live IPs failed: " + repr(e))
        return out
