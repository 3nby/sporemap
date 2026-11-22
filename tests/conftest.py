import os

import pytest

from sporemap.db.engine import get_engine
from sporemap.settings import get_settings


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # keep tests out of the real data dir + away from any local sporemap.yaml
    for k in list(os.environ):
        if k.startswith("SPOREMAP_"):  # real secrets/settings in your shell would beat the test files
            monkeypatch.delenv(k)
    monkeypatch.setenv("SPOREMAP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPOREMAP_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.chdir(tmp_path)  # so a real .env in the repo never leaks into tests
    get_settings.cache_clear()
    get_engine.cache_clear()  # fresh db per test
    yield
    get_engine().dispose()
    get_engine.cache_clear()
    get_settings.cache_clear()
