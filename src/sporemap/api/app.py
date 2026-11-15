from fastapi import APIRouter, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sporemap import __version__
from sporemap.db import get_engine, migrate
from sporemap.settings import get_settings

r = APIRouter(prefix="/api/v1")


@r.get("/health")
def health():
    db = "ok"
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
    except SQLAlchemyError:  # dont leak details here
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "version": __version__, "db": db}


def create_app():
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)  # db + keys live here
    migrate()  # no-op when already up to date

    a = FastAPI(title="Sporemap", version=__version__)
    a.include_router(r)
    # print("app built")
    return a
