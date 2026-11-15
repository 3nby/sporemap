from pydantic import BaseModel

from sporemap.sources.base import Link, Obs, Result, Source


class DemoConfig(BaseModel):
    pass  # nothing to configure, its fake


class Demo(Source):
    """A small fake lab so you can try Sporemap with nothing connected."""

    name = "demo"
    Config = DemoConfig

    async def test(self, cfg):
        return None

    async def discover(self, cfg):
        o = [
            Obs("host", "node/pve1", "pve1", {"ip": "192.168.1.10"}, ["ip:192.168.1.10", "mac:aa:bb:cc:00:00:01"]),
            Obs("network", "net/vmbr0", "vmbr0", {"cidr": "192.168.1.0/24"}),
            Obs("workload", "lxc/100", "proxy", {"vmid": 100, "ip": "192.168.1.20"}, ["ip:192.168.1.20"]),
            Obs("workload", "lxc/101", "web", {"vmid": 101, "ip": "192.168.1.21"}, ["ip:192.168.1.21"]),
            Obs("app", "app/caddy", "Caddy", {"port": 80}),
            Obs("app", "app/site", "Website", {"port": 8080}, ["ip:192.168.1.21:8080"]),
            Obs("endpoint", "dns/example.lan", "example.lan", {}, ["fqdn:example.lan"]),
        ]
        ln = [
            Link("hosts", "node/pve1", "lxc/100"),
            Link("hosts", "node/pve1", "lxc/101"),
            Link("member_of", "lxc/100", "net/vmbr0"),
            Link("member_of", "lxc/101", "net/vmbr0"),
            Link("runs", "lxc/100", "app/caddy"),
            Link("runs", "lxc/101", "app/site"),
            Link("routes_to", "dns/example.lan", "app/caddy"),
            Link("routes_to", "app/caddy", "ip:192.168.1.21:8080"),  # resolved by key later
        ]
        return Result(o, ln).check()
