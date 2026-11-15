import pytest
from pydantic import BaseModel, SecretStr

from sporemap.settings import get_settings
from sporemap.sources.config import SourceConfigError, build_config, env_name, redacted


class Cfg(BaseModel):
    host: str
    port: int = 8006
    token_secret: SecretStr


class Fake:
    Config = Cfg


def _yaml(tmp_path, monkeypatch, text):
    f = tmp_path / "s.yaml"
    f.write_text(text)
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    get_settings.cache_clear()


def test_env_name():
    assert env_name("proxmox", "token_secret") == "SPOREMAP_PROXMOX_TOKEN_SECRET"
    assert env_name("cloudflared-file", "key") == "SPOREMAP_CLOUDFLARED_FILE_KEY"


def test_yaml_plus_env_secret(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: 10.0.0.1\n")
    monkeypatch.setenv("SPOREMAP_FAKE_TOKEN_SECRET", "hunter2")
    c = build_config("fake", Fake)
    assert c.host == "10.0.0.1" and c.port == 8006
    assert c.token_secret.get_secret_value() == "hunter2"


def test_dotenv_file(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: h\n")
    (tmp_path / ".env").write_text("SPOREMAP_FAKE_TOKEN_SECRET=fromfile\n")
    assert build_config("fake", Fake).token_secret.get_secret_value() == "fromfile"


def test_real_env_beats_dotenv(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: h\n")
    (tmp_path / ".env").write_text("SPOREMAP_FAKE_TOKEN_SECRET=fromfile\n")
    monkeypatch.setenv("SPOREMAP_FAKE_TOKEN_SECRET", "fromenv")
    assert build_config("fake", Fake).token_secret.get_secret_value() == "fromenv"


def test_secret_in_yaml_is_refused(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: h\n    token_secret: oops\n")
    with pytest.raises(SourceConfigError, match="SPOREMAP_FAKE_TOKEN_SECRET"):
        build_config("fake", Fake)


def test_missing_field_is_a_clear_error(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: h\n")
    with pytest.raises(SourceConfigError, match="token_secret"):
        build_config("fake", Fake)


def test_redacted_hides_secret(tmp_path, monkeypatch):
    _yaml(tmp_path, monkeypatch, "sources:\n  fake:\n    host: h\n")
    monkeypatch.setenv("SPOREMAP_FAKE_TOKEN_SECRET", "hunter2")
    r = redacted("fake", Fake)
    assert r["token_secret"] == "********" and "hunter2" not in str(r)


def test_settings_read_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SPOREMAP_PORT=9999\n")
    get_settings.cache_clear()
    assert get_settings().port == 9999
