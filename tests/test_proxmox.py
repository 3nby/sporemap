import asyncio
import json
from pathlib import Path

import httpx2
import pytest

from sporemap.sources.proxmox import Proxmox, ProxmoxConfig, disk_storage, kv, nic_info

FIX = Path(__file__).parent / "fixtures" / "proxmox"
GOLDEN = Path(__file__).parent / "golden"


def fake_api(fixture, overrides=None, seen=None):
    data = json.loads((FIX / fixture).read_text())
    data.update(overrides or {})

    def handler(req):
        if seen is not None:
            seen.append(req)
        path = req.url.path.removeprefix("/api2/json")
        v = data.get(path)
        if isinstance(v, int):  # a bare status code means "fail like this"
            return httpx2.Response(v, json={"data": None})
        if v is None:
            return httpx2.Response(404, json={"data": None})
        return httpx2.Response(200, json={"data": v})

    return httpx2.MockTransport(handler)


def run(fixture="standalone.json", overrides=None, seen=None):
    p = Proxmox()
    p.transport = fake_api(fixture, overrides, seen)
    cfg = ProxmoxConfig(host="pve.test", token_id="sporemap@pve!scan", token_secret="s3cret", verify_ssl=False)
    return asyncio.run(p.discover(cfg))


def summary(res):
    return {
        "obs": sorted([o.kind, o.ext_id, o.name, sorted(o.keys)] for o in res.obs),
        "links": sorted([ln.kind, ln.src, ln.dst] for ln in res.links),
    }


def test_standalone_matches_golden():
    res = run()
    want = json.loads((GOLDEN / "proxmox_standalone.json").read_text())
    assert summary(res) == want
    assert res.warnings == []


def test_sends_token_header():
    seen = []
    run(seen=seen)
    assert seen[0].headers["authorization"] == "PVEAPIToken=sporemap@pve!scan=s3cret"
    assert seen[0].url.host == "pve.test" and seen[0].url.port == 8006


def test_template_skipped_and_stopped_vm_kept():
    ids = {o.ext_id for o in run().obs}
    assert "qemu/9000" not in ids
    assert "qemu/300" in ids


def test_live_ip_beats_dhcp():
    pub = next(o for o in run().obs if o.ext_id == "lxc/202")
    assert pub.attrs["ips"] == ["192.168.1.71"]
    assert "ip:192.168.1.71" in pub.keys


def test_bind_mount_and_cdrom_ignored():
    links = {(ln.src, ln.dst) for ln in run().links if ln.kind == "mounts"}
    assert ("lxc/201", "storage/ghostprox/local-lvm") in links
    assert ("qemu/300", "storage/ghostprox/local") not in links  # that was the iso


def test_401_is_a_clear_error():
    with pytest.raises(PermissionError, match="401"):
        run(overrides={"/cluster/status": 401})


def test_agent_403_is_a_warning_not_a_failure():
    res = run(overrides={
        "/cluster/resources": json.loads((FIX / "standalone.json").read_text())["/cluster/resources"][:2] + [
            {"type": "qemu", "id": "qemu/301", "vmid": 301, "name": "vm", "node": "ghostprox", "status": "running"}],
        "/nodes/ghostprox/qemu/301/config": {"agent": "1", "net0": "virtio=BC:24:11:00:01:2D,bridge=vmbr0"},
        "/nodes/ghostprox/qemu/301/agent/network-get-interfaces": 403,
    })
    assert any("VM.GuestAgent.Audit" in w for w in res.warnings)
    assert any(o.ext_id == "qemu/301" for o in res.obs)


def test_agent_ips_used_when_allowed():
    res = run(overrides={
        "/cluster/resources": json.loads((FIX / "standalone.json").read_text())["/cluster/resources"][:2] + [
            {"type": "qemu", "id": "qemu/301", "vmid": 301, "name": "vm", "node": "ghostprox", "status": "running"}],
        "/nodes/ghostprox/qemu/301/config": {"agent": "1", "net0": "virtio=BC:24:11:00:01:2D,bridge=vmbr0"},
        "/nodes/ghostprox/qemu/301/agent/network-get-interfaces": {"result": [
            {"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
            {"name": "eth0", "ip-addresses": [{"ip-address": "192.168.1.80", "ip-address-type": "ipv4"},
                                              {"ip-address": "fe80::1", "ip-address-type": "ipv6"}]}]},
    })
    vm = next(o for o in res.obs if o.ext_id == "qemu/301")
    assert vm.attrs["ips"] == ["192.168.1.80"]


def test_offline_node_still_listed():
    res = run(overrides={
        "/cluster/resources": [{"type": "node", "id": "node/ghostprox", "node": "ghostprox", "status": "offline"},
                               {"type": "lxc", "id": "lxc/200", "vmid": 200, "name": "caddy", "node": "ghostprox",
                                "status": "unknown"}],
    })
    assert {o.ext_id for o in res.obs} == {"node/ghostprox", "lxc/200"}


def test_parsers():
    assert kv("name=eth0,bridge=vmbr2") == {"name": "eth0", "bridge": "vmbr2"}
    assert nic_info("virtio=BC:24:11:00:01:2C,bridge=vmbr0,firewall=1") == {
        "mac": "bc:24:11:00:01:2c", "bridge": "vmbr0", "ip": None}
    assert nic_info("name=eth0,hwaddr=AA:BB:CC:DD:EE:FF,ip=10.0.0.5/24")["ip"] == "10.0.0.5"
    assert disk_storage("local-lvm:vm-1-disk-0,size=4G") == "local-lvm"
    assert disk_storage("/mnt/x,mp=/x") is None
    assert disk_storage("local:iso/a.iso,media=cdrom") is None
    assert disk_storage("none,media=cdrom") is None


def test_cli_sources_test_reports_failure(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from sporemap.cli import app
    f = tmp_path / "s.yaml"
    f.write_text("sources:\n  proxmox:\n    host: 127.0.0.1\n    port: 1\n    token_id: a@pve!b\n    timeout: 1\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    monkeypatch.setenv("SPOREMAP_PROXMOX_TOKEN_SECRET", "x")
    from sporemap.settings import get_settings
    get_settings.cache_clear()
    r = CliRunner().invoke(app, ["sources", "test", "proxmox"])
    assert r.exit_code == 1 and "FAILED" in r.output


def test_docker_and_virtual_ifaces_ignored():
    res = run(overrides={"/nodes/ghostprox/lxc/201/interfaces": [
        {"name": "lo", "inet": "127.0.0.1/8"},
        {"name": "eth0", "inet": "192.168.1.69/24"},
        {"name": "docker0", "inet": "172.17.0.1/16"},
        {"name": "br-1a2b3c", "inet": "172.18.0.1/16"},
        {"name": "veth9f8e", "inet": "169.254.1.1/16"},
        {"name": "tailscale0", "inet": "100.120.192.39/32"},
    ]})
    ct = next(o for o in res.obs if o.ext_id == "lxc/201")
    assert ct.attrs["ips"] == ["192.168.1.69", "100.120.192.39"]
    assert not any(k.startswith("ip:172.") for k in ct.keys)
