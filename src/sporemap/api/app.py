from fastapi import APIRouter, FastAPI

from sporemap import __version__

r = APIRouter(prefix="/api/v1")


@r.get("/health")
def health():
    # just says we're alive for now, db check later
    return {"status": "ok", "version": __version__}


def create_app():
    a = FastAPI(title="Sporemap", version=__version__)
    a.include_router(r)
    # print("app built")
    return a
