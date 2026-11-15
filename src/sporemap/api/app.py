from fastapi import APIRouter, FastAPI

from sporemap import __version__
from sporemap.settings import get_settings

r = APIRouter(prefix="/api/v1")


@r.get("/health")
def health():
    # just says we're alive for now, db check later
    return {"status": "ok", "version": __version__}


def create_app():
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)  # db + keys will live here

    a = FastAPI(title="Sporemap", version=__version__)
    a.include_router(r)
    # print("app built")
    return a
