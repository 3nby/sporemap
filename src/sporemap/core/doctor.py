"""sporemap doctor: test every configured source and say how to fix what's broken."""
import asyncio
import socket
import ssl

import asyncssh
import httpx2
from pydantic import ValidationError

from sporemap.settings import _cfg_path, env_files, get_settings
from sporemap.sources import load_sources
from sporemap.sources.config import (
    SourceConfigError,
    _is_secret,
    build_config,
    env_name,
)

# what to do when a source's credentials get a 403, keyed by source
PERMS = {
    "proxmox": "give the token read access, eg: pveum acl modify / --tokens <token_id> --roles PVEAuditor",
    "cloudflare": "the token needs Zone:Zone:Read, Zone:DNS:Read and Account:Cloudflare Tunnel:Read",
    "docker": "enable the endpoints on the socket proxy (CONTAINERS=1, INFO=1)",
}


def _env_target():
    # where a secret should go: the env file next to the yaml when installed, else .env
    fs = env_files()
    return str(fs[0]) if fs else str(_cfg_path().parent / "sporemap.env") + " (or .env)"


def _chain(e):
    # e, then whatever caused it, then what caused that...
    seen = []
    while e is not None and e not in seen:
        seen.append(e)
        e = e.__cause__ or e.__context__
    return seen


def _secret_names(name, cls):
    return [env_name(name, f) for f, fi in cls.Config.model_fields.items() if _is_secret(fi)]


def config_hint(name, cls, e):
    """Hint for a SourceConfigError, from the ValidationError underneath it if theres one."""
    if "is a secret" in str(e):
        return "delete it from " + str(_cfg_path()) + " and put it in " + _env_target()
    ve = e.__cause__ if isinstance(e.__cause__, ValidationError) else None
    if ve is None:
        return None
    fields = cls.Config.model_fields
    out = []
    for err in ve.errors():
        f = str(err["loc"][0]) if err["loc"] else ""
        if err["type"] == "missing" and f in fields and _is_secret(fields[f]):
            out.append("set " + env_name(name, f) + " in " + _env_target())
        elif err["type"] == "missing":
            out.append("add " + f + " under sources." + name + " in " + str(_cfg_path()))
        else:
            loc = ".".join(str(x) for x in err["loc"])
            out.append("fix sources." + name + "." + loc + " in " + str(_cfg_path()) + " (" + err["msg"] + ")")
    return "; ".join(out) or None


def hint(name, cls, e):
    """Best guess at how to fix a failed source test. None when we have no idea."""
    for x in _chain(e):
        if isinstance(x, httpx2.HTTPStatusError):
            code = x.response.status_code
            if code == 401:
                return _creds(name, cls)
            if code == 403:
                return PERMS.get(name, "the credentials work but arent allowed to read that, check their permissions")
            if code == 404:
                return "got a 404, check the url/host points at the right service"
        if isinstance(x, PermissionError) and "401" in str(x):
            return _creds(name, cls)
        if isinstance(x, PermissionError) and name in PERMS:
            return PERMS[name]
        if isinstance(x, asyncssh.HostKeyNotVerifiable):
            return ("the ssh host key isnt trusted yet. as the user sporemap runs as: ssh-keyscan -H <host> >> ~/.ssh/known_hosts"
                    " (or point known_hosts at a file that has it)")
        if isinstance(x, asyncssh.PermissionDenied):
            return "ssh login refused. check key_path and that its .pub is in authorized_keys for that user on the box"
        if isinstance(x, FileNotFoundError):
            return "a file is missing (" + str(x.filename or x) + "), check key_path / known_hosts"
        if isinstance(x, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(x):
            return "the cert isnt trusted. self-signed (proxmox default)? set verify_ssl: false for this source"
        if isinstance(x, ConnectionRefusedError) or "refused" in str(x).lower():
            return "nothing is listening there. check the host and port, that the service is up, and the firewall"
        if isinstance(x, socket.gaierror) or "name or service not known" in str(x).lower() or "getaddrinfo" in str(x):
            return "that hostname doesnt resolve, use an ip or fix dns"
        if isinstance(x, (TimeoutError, httpx2.TimeoutException)):
            return "no answer in time. wrong ip, host down, or no route from here (vlan / bridge / firewall)"
        if isinstance(x, (OSError, httpx2.ConnectError)) and "unreachable" in str(x).lower():
            return "no route to that host from here. check the ip and which bridge/vlan this box is on"
    return None


def _creds(name, cls):
    s = _secret_names(name, cls)
    if s:
        return "the credentials were rejected. check " + " and ".join(s) + " (and any token id in the yaml), and that the token still exists"
    return "the credentials were rejected"


async def check(name, sources=None):
    """Test one source. Returns {"name", "ok", "error", "hint"}."""
    srcs = sources if sources is not None else load_sources()
    out = {"name": name, "ok": False, "error": None, "hint": None}
    if name not in srcs:
        out["error"] = "not installed"
        out["hint"] = "check the name in sporemap.yaml. installed: " + ", ".join(sorted(srcs))
        return out
    cls = srcs[name]
    try:
        cfg = build_config(name, cls)
    except SourceConfigError as e:
        out["error"] = str(e)
        out["hint"] = config_hint(name, cls, e)
        return out
    t = get_settings().scan_timeout
    try:
        await asyncio.wait_for(cls().test(cfg), timeout=t)
    except TimeoutError as e:
        out["error"] = "timed out after " + str(t) + "s"
        out["hint"] = hint(name, cls, e)
        return out
    except Exception as e:  # noqa: BLE001  plugin code can throw anything
        out["error"] = type(e).__name__ + (": " + str(e) if str(e) else "")  # httpx timeouts have no message
        out["hint"] = hint(name, cls, e)
        return out
    out["ok"] = True
    return out


async def check_all(names=None, sources=None):
    srcs = sources if sources is not None else load_sources()
    if names is None:
        names = sorted(get_settings().sources)
    # one after another, keeps output in order and doesnt hammer anything
    return [await check(n, sources=srcs) for n in names]
