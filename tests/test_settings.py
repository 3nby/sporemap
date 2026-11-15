from pathlib import Path

from sporemap.settings import Settings


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
