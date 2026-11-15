import pytest

from sporemap.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # keep tests out of the real data dir + away from any local sporemap.yaml
    monkeypatch.setenv("SPOREMAP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPOREMAP_CONFIG", str(tmp_path / "none.yaml"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
