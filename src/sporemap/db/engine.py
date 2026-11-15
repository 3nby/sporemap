from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlmodel import create_engine

from sporemap.settings import get_settings

MIG = Path(__file__).parent / "migrations"


def db_url():
    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)  # sqlite wont make the folder for us
    return "sqlite:///" + str(d / "sporemap.db")


@lru_cache
def get_engine():
    e = create_engine(db_url())

    @event.listens_for(e, "connect")
    def _pragmas(conn, _rec):
        c = conn.cursor()
        c.execute("PRAGMA journal_mode=WAL")  # readers dont block the correlator
        c.execute("PRAGMA foreign_keys=ON")  # sqlite has these off by default, lol
        c.close()

    return e


def _cfg(url):
    c = Config()
    c.set_main_option("script_location", str(MIG))
    c.set_main_option("sqlalchemy.url", url)
    return c


def migrate(url=None):
    # bring the db up to the latest schema, safe to run every startup
    u = url or db_url()
    # print("migrating " + u)
    command.upgrade(_cfg(u), "head")
