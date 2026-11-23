"""One admin password for the web UI and API. Stdlib only.

The password itself is never stored, only an scrypt hash in SPOREMAP_PASSWORD_HASH
(sporemap.env next to the yaml). Signing in hands out a signed, httponly cookie good for
30 days. Changing the password signs everyone out. No hash set = auth off, which is fine
on 127.0.0.1 and gets a warning anywhere else.
"""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from sporemap.settings import _cfg_path, get_settings

COOKIE = "sporemap_session"
DAYS = 30
# brute force brake: this many wrong passwords from one address in WINDOW seconds locks it for LOCK
TRIES, WINDOW, LOCK = 5, 600, 300
# open even with auth on: monitoring, and what the login page itself needs
OPEN = {"/api/v1/health", "/api/v1/auth/status", "/api/v1/auth/login", "/api/v1/auth/logout"}
GUARDED = ("/api/", "/docs", "/redoc", "/openapi.json")

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    # colons not $, dotenv files treat $ specially
    return "scrypt:" + str(_N) + ":" + str(_R) + ":" + str(_P) + ":" + salt.hex() + ":" + dk.hex()


def check_password(pw, stored):
    try:
        algo, n, r, p, salt, want = stored.split(":")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(want) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


def stored_hash():
    h = get_settings().password_hash
    return h.get_secret_value() if h else None


def enabled():
    return bool(stored_hash())


def exposed():
    # listening beyond this machine?
    return get_settings().host not in ("127.0.0.1", "localhost", "::1")


def _key():
    # signing key for session cookies, made once and kept next to the db
    p = get_settings().data_dir / "session.key"
    try:
        return bytes.fromhex(p.read_text().strip())
    except (FileNotFoundError, ValueError):
        k = secrets.token_bytes(32)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(k.hex())
        return k


def _fp(key, h):
    # ties a session to the current password, so changing it signs everyone out
    return hmac.new(key, h.encode(), hashlib.sha256).hexdigest()[:16]


def make_token(now=None):
    key = _key()
    body = str(int(now if now is not None else time.time())) + "." + _fp(key, stored_hash() or "")
    return body + "." + hmac.new(key, body.encode(), hashlib.sha256).hexdigest()


def token_ok(tok):
    if not tok or not stored_hash():
        return False
    try:
        ts, fp, sig = tok.split(".")
        key = _key()
        body = ts + "." + fp
        if not hmac.compare_digest(sig, hmac.new(key, body.encode(), hashlib.sha256).hexdigest()):
            return False
        if not hmac.compare_digest(fp, _fp(key, stored_hash())):
            return False  # password changed since
        return 0 <= time.time() - int(ts) < DAYS * 86400
    except ValueError:
        return False


def write_hash(h):
    """Put (or with None, take out) SPOREMAP_PASSWORD_HASH in sporemap.env next to the yaml."""
    p = _cfg_path().parent / "sporemap.env"
    lines = p.read_text().splitlines() if p.exists() else []
    lines = [ln for ln in lines if not ln.startswith("SPOREMAP_PASSWORD_HASH=")]
    if h:
        lines.append("SPOREMAP_PASSWORD_HASH=" + h)
    new = not p.exists()
    p.write_text("\n".join(lines) + "\n")
    if new:
        os.chmod(p, 0o600)  # holds secrets. an existing file keeps its owner + mode
    get_settings.cache_clear()
    return p


# --- the guard ------------------------------------------------------------------

async def guard(request: Request, call_next):
    """Middleware: with a password set, api calls + docs need a valid session cookie."""
    path = request.url.path
    locked = enabled() and path not in OPEN and path.startswith(GUARDED)
    if locked and not token_ok(request.cookies.get(COOKIE)):
        # print("401 for " + path)
        return Response('{"detail":"sign in first"}', status_code=401, media_type="application/json")
    return await call_next(request)


# --- the api the login page uses -----------------------------------------------

r = APIRouter()
_fails = defaultdict(list)  # address -> times of wrong passwords


class LoginIn(BaseModel):
    password: str


def _who(req):
    return req.client.host if req.client else "?"


@r.get("/auth/status")
def status(request: Request):
    return {"enabled": enabled(), "signed_in": token_ok(request.cookies.get(COOKIE)), "exposed": exposed()}


@r.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    if not enabled():
        raise HTTPException(400, "no password is set, run: sporemap set-password")
    who = _who(request)
    now = time.time()
    _fails[who] = [t for t in _fails[who] if now - t < WINDOW]
    if len(_fails[who]) >= TRIES and now - _fails[who][-1] < LOCK:
        raise HTTPException(429, "too many wrong passwords, try again in a few minutes")
    if not check_password(body.password, stored_hash()):
        _fails[who].append(now)
        time.sleep(0.4)  # costs a guesser time, costs you nothing
        raise HTTPException(401, "wrong password")
    _fails.pop(who, None)
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE, make_token(), max_age=DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")
    return {"ok": True}


@r.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
