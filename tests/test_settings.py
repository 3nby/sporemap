from pathlib import Path

from sporemap.settings import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8420


def test_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOREMAP_PORT", "9000")
    monkeypatch.setenv("SPOREMAP_DATA_DIR", str(tmp_path / "d"))
    s = Settings()
    assert s.port == 9000
    assert s.data_dir == tmp_path / "d"


def test_yaml_file(monkeypatch, tmp_path):
    f = tmp_path / "cfg.yaml"
    f.write_text("port: 7000\nhost: 0.0.0.0\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    s = Settings()
    assert s.port == 7000
    assert s.host == "0.0.0.0"


def test_env_beats_yaml(monkeypatch, tmp_path):
    f = tmp_path / "cfg.yaml"
    f.write_text("port: 7000\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    monkeypatch.setenv("SPOREMAP_PORT", "9000")
    assert Settings().port == 9000


def test_missing_yaml_is_fine(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOREMAP_CONFIG", str(tmp_path / "nope.yaml"))
    assert Settings().port == 8420
    assert isinstance(Settings().data_dir, Path)


def test_empty_sources_in_yaml_are_ok(tmp_path, monkeypatch):
    # every source commented out (like the example file), and a source with no settings at all
    f = tmp_path / "s.yaml"
    f.write_text("sources:\n  # demo: {}\n")
    monkeypatch.setenv("SPOREMAP_CONFIG", str(f))
    get_settings.cache_clear()
    assert get_settings().sources == {}
    f.write_text("sources:\n  demo:\n  caddy:\n    host_ip: 10.0.0.1\n")
    get_settings.cache_clear()
    assert get_settings().sources == {"demo": {}, "caddy": {"host_ip": "10.0.0.1"}}


def test_example_yaml_loads(monkeypatch):
    ex = Path(__file__).parent.parent / "sporemap.example.yaml"
    monkeypatch.setenv("SPOREMAP_CONFIG", str(ex))
    get_settings.cache_clear()
    assert get_settings().sources == {}  # nothing on by default, not even demo


def test_etc_fallback(monkeypatch, tmp_path):
    # installed layout: no SPOREMAP_CONFIG, nothing in cwd, config + secrets in /etc/sporemap
    from sporemap import settings
    from sporemap.sources.config import build_config
    from sporemap.sources.proxmox import Proxmox

    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "sporemap.yaml").write_text("port: 7100\nsources:\n  proxmox:\n    host: 10.0.0.5\n    token_id: a@pve!b\n")
    (etc / "sporemap.env").write_text("SPOREMAP_PROXMOX_TOKEN_SECRET=shh\n")
    monkeypatch.delenv("SPOREMAP_CONFIG")
    monkeypatch.setattr(settings, "ETC", etc)
    get_settings.cache_clear()
    assert get_settings().port == 7100
    assert build_config("proxmox", Proxmox).token_secret.get_secret_value() == "shh"


def test_cwd_yaml_beats_etc(monkeypatch, tmp_path):
    from sporemap import settings

    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "sporemap.yaml").write_text("port: 7100\n")
    (tmp_path / "sporemap.yaml").write_text("port: 7200\n")  # cwd is tmp_path in tests
    monkeypatch.delenv("SPOREMAP_CONFIG")
    monkeypatch.setattr(settings, "ETC", etc)
    get_settings.cache_clear()
    assert get_settings().port == 7200


def test_dotenv_beats_sporemap_env(monkeypatch, tmp_path):
    from sporemap.sources.config import build_config
    from sporemap.sources.proxmox import Proxmox

    (tmp_path / "sporemap.env").write_text("SPOREMAP_PROXMOX_TOKEN_SECRET=old\n")  # next to none.yaml
    (tmp_path / ".env").write_text("SPOREMAP_PROXMOX_TOKEN_SECRET=new\n")
    monkeypatch.setenv("SPOREMAP_PROXMOX_HOST", "h")
    monkeypatch.setenv("SPOREMAP_PROXMOX_TOKEN_ID", "a@pve!b")
    get_settings.cache_clear()
    assert build_config("proxmox", Proxmox).token_secret.get_secret_value() == "new"
