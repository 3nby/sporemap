import asyncio
import socket
import ssl

import asyncssh
import httpx2
import pytest
from typer.testing import CliRunner

from sporemap.cli import app
from sporemap.core.doctor import check, check_all, hint
from sporemap.settings import get_settings
from sporemap.sources.cloudflared import Cloudflared
from sporemap.sources.demo import Demo
from sporemap.sources.docker import Docker
from sporemap.sources.proxmox import Proxmox

PVE = "sources:\n  proxmox:\n    host: pve.test\n    token_id: a@pve!b\n"


def _yaml(monkeypatch, tmp_path, txt):
    p = tmp_path / "cfg.yaml"
    p.write_text(txt)
    monkeypatch.setenv("SPOREMAP_CONFIG", str(p))
    get_settings.cache_clear()
    return p


def _status(code):
    req = httpx2.Request("GET", "https://pve.test/api2/json/version")
    return httpx2.HTTPStatusError("nope", request=req, response=httpx2.Response(code, request=req))


def _wrapped(inner, outer):
    # like httpx does: raise ConnectError from the socket error
    try:
        try:
            raise inner
        except Exception as e:
            raise outer from e
    except Exception as e:  # noqa: BLE001
        return e


def test_hint_401_names_the_secret():
    h = hint("proxmox", Proxmox, _status(401))
    assert "SPOREMAP_PROXMOX_TOKEN_SECRET" in h
    assert "SPOREMAP_PROXMOX_TOKEN_SECRET" in hint("proxmox", Proxmox, PermissionError("proxmox rejected the API token (401)"))


def test_hint_403_is_per_source():
    assert "PVEAuditor" in hint("proxmox", Proxmox, _status(403))
    assert "CONTAINERS=1" in hint("docker", Docker, PermissionError("docker proxy refused /info"))


@pytest.mark.parametrize("e,bit", [
    (asyncssh.HostKeyNotVerifiable("Host key is not trusted"), "ssh-keyscan"),
    (asyncssh.PermissionDenied("Permission denied"), "authorized_keys"),
    (ConnectionRefusedError(111, "Connect call failed"), "nothing is listening"),
    (TimeoutError(), "no answer"),
    (socket.gaierror(-2, "Name or service not known"), "doesnt resolve"),
    (ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"), "verify_ssl: false"),
    (FileNotFoundError(2, "No such file", "/root/.ssh/sporemap"), "/root/.ssh/sporemap"),
])
def test_hint_for_common_failures(e, bit):
    assert bit in hint("cloudflared", Cloudflared, e)


def test_hint_digs_through_wrapped_errors():
    e = _wrapped(ConnectionRefusedError(111, "Connect call failed"), httpx2.ConnectError("All connection attempts failed"))
    assert "nothing is listening" in hint("proxmox", Proxmox, e)


def test_hint_unknown_is_none():
    assert hint("demo", Demo, RuntimeError("weird")) is None


def test_missing_secret(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, PVE)
    x = asyncio.run(check("proxmox", sources={"proxmox": Proxmox}))
    assert not x["ok"] and "token_secret" in x["error"]
    assert "set SPOREMAP_PROXMOX_TOKEN_SECRET in" in x["hint"] and "sporemap.env" in x["hint"]


def test_missing_setting(monkeypatch, tmp_path):
    p = _yaml(monkeypatch, tmp_path, "sources:\n  proxmox:\n    token_id: a@pve!b\n")
    monkeypatch.setenv("SPOREMAP_PROXMOX_TOKEN_SECRET", "x")
    x = asyncio.run(check("proxmox", sources={"proxmox": Proxmox}))
    assert x["hint"] == "add host under sources.proxmox in " + str(p)


def test_bad_value(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, PVE + "    port: lots\n")
    monkeypatch.setenv("SPOREMAP_PROXMOX_TOKEN_SECRET", "x")
    x = asyncio.run(check("proxmox", sources={"proxmox": Proxmox}))
    assert "fix sources.proxmox.port" in x["hint"]


def test_secret_in_yaml(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, PVE + "    token_secret: oops\n")
    x = asyncio.run(check("proxmox", sources={"proxmox": Proxmox}))
    assert "delete it from" in x["hint"]


def test_rejected_token(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, PVE)
    monkeypatch.setenv("SPOREMAP_PROXMOX_TOKEN_SECRET", "wrong")
    monkeypatch.setattr(Proxmox, "transport", httpx2.MockTransport(lambda req: httpx2.Response(401)))
    x = asyncio.run(check("proxmox", sources={"proxmox": Proxmox}))
    assert not x["ok"] and "401" in x["error"]
    assert "SPOREMAP_PROXMOX_TOKEN_SECRET" in x["hint"]


def test_check_all(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n  ghost:\n")
    res = asyncio.run(check_all(sources={"demo": Demo}))
    assert [(x["name"], x["ok"]) for x in res] == [("demo", True), ("ghost", False)]
    assert "installed: demo" in res[1]["hint"]


def test_cli_doctor_ok(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n")
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 0, r.output
    assert "database: ok" in r.output and "demo: ok" in r.output


def test_cli_doctor_fails_with_hint(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "sources:\n  demo:\n  nosuch:\n")
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 1
    assert "demo: ok" in r.output and "nosuch: FAILED not installed" in r.output and "fix:" in r.output


def test_cli_doctor_no_sources(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "port: 8420\n")
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 1 and "none configured" in r.output


def test_cli_doctor_bad_config(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, "scan_every: whenever\n")
    r = CliRunner().invoke(app, ["doctor"])
    assert r.exit_code == 1 and "config: FAILED" in r.output
    _yaml(monkeypatch, tmp_path, "")  # so conftest teardown can still load settings


def test_cli_sources_test_shows_hint(monkeypatch, tmp_path):
    _yaml(monkeypatch, tmp_path, PVE)
    r = CliRunner().invoke(app, ["sources", "test", "proxmox"])
    assert r.exit_code == 1 and "fix: set SPOREMAP_PROXMOX_TOKEN_SECRET" in r.output
