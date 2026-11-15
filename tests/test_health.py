from fastapi.testclient import TestClient

from sporemap import __version__
from sporemap.api.app import create_app


def test_health():
    c = TestClient(create_app())
    res = c.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "version": __version__, "db": "ok"}
