import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from sporemap import auth
from sporemap.api.app import create_app
from sporemap.cli import app as cli
from sporemap.settings import get_settings

PW = "test-pass-123"  # a throwaway for these tests only


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)  # the wrong-password delay
    auth._fails.clear()


def _lock(monkeypatch, pw=PW):
    monkeypatch.setenv("SPOREMAP_PASSWORD_HASH", auth.hash_password(pw))
    get_settings.cache_clear()


def test_hash_roundtrip():
    h = auth.hash_password(PW)
    assert h.startswith("scrypt:") and PW not in h
    assert auth.check_password(PW, h)
    assert not auth.check_password("nope", h)
    assert not auth.check_password(PW, "garbage")
    assert auth.hash_password(PW) != h  # salted


def test_no_password_means_open():
    c = TestClient(create_app())
    assert c.get("/api/v1/graph").status_code == 200
    assert c.get("/api/v1/auth/status").json() == {"enabled": False, "signed_in": False, "exposed": False}


def test_locked_until_signed_in(monkeypatch):
    _lock(monkeypatch)
    c = TestClient(create_app())
    assert c.get("/api/v1/graph").status_code == 401
    assert c.get("/api/v1/overview").status_code == 401
    assert c.get("/docs").status_code == 401 and c.get("/openapi.json").status_code == 401
    assert c.get("/api/v1/health").status_code == 200  # monitoring stays open
    assert c.get("/").status_code == 200  # the page shell has no lab data, the api does
    assert c.get("/api/v1/auth/status").json()["signed_in"] is False

    assert c.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    res = c.post("/api/v1/auth/login", json={"password": PW})
    assert res.status_code == 200
    ck = res.headers["set-cookie"].lower()
    assert "httponly" in ck and "samesite=lax" in ck
    assert c.get("/api/v1/graph").status_code == 200
    assert c.get("/api/v1/auth/status").json()["signed_in"] is True

    c.post("/api/v1/auth/logout")
    assert c.get("/api/v1/graph").status_code == 401


def test_new_password_signs_everyone_out(monkeypatch):
    _lock(monkeypatch)
    c = TestClient(create_app())
    c.post("/api/v1/auth/login", json={"password": PW})
    assert c.get("/api/v1/graph").status_code == 200
    _lock(monkeypatch, "another-pass-456")
    assert c.get("/api/v1/graph").status_code == 401


def test_tokens_expire_and_cant_be_forged(monkeypatch):
    _lock(monkeypatch)
    assert auth.token_ok(auth.make_token())
    assert not auth.token_ok(auth.make_token(now=time.time() - 31 * 86400))
    ts, fp, sig = auth.make_token().split(".")
    assert not auth.token_ok(ts + "." + fp + "." + "0" * len(sig))
    assert not auth.token_ok(str(int(time.time()) + 999) + "." + fp + "." + sig)  # cant move the date
    assert not auth.token_ok("") and not auth.token_ok("a.b")


def test_too_many_wrong_passwords(monkeypatch):
    _lock(monkeypatch)
    c = TestClient(create_app())
    for _ in range(auth.TRIES):
        assert c.post("/api/v1/auth/login", json={"password": "guess"}).status_code == 401
    # locked out now, even the right password waits
    assert c.post("/api/v1/auth/login", json={"password": PW}).status_code == 429


def test_cli_set_password(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("port: 8420\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(cfg))
    get_settings.cache_clear()
    r = CliRunner().invoke(cli, ["set-password"], input=PW + "\n" + PW + "\n")
    assert r.exit_code == 0, r.output
    env = (tmp_path / "sporemap.env").read_text()
    assert PW not in env and "SPOREMAP_PASSWORD_HASH=scrypt:" in env
    get_settings.cache_clear()
    assert auth.enabled() and auth.check_password(PW, auth.stored_hash())

    assert CliRunner().invoke(cli, ["set-password"], input="short\nshort\n").exit_code == 1
    assert CliRunner().invoke(cli, ["set-password", "--off"]).exit_code == 0
    assert "SPOREMAP_PASSWORD_HASH" not in (tmp_path / "sporemap.env").read_text()


def test_doctor_flags_an_open_door(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("host: 0.0.0.0\nsources:\n  demo:\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(cfg))
    get_settings.cache_clear()
    r = CliRunner().invoke(cli, ["doctor"])
    assert r.exit_code == 1 and "login: FAILED" in r.output
    _lock(monkeypatch)
    r = CliRunner().invoke(cli, ["doctor"])
    assert r.exit_code == 0 and "login: password set" in r.output
