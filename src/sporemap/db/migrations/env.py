from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import sporemap.db.models  # noqa: F401  registers the tables

target = SQLModel.metadata
cfg = context.config


def run_offline():
    context.configure(url=cfg.get_main_option("sqlalchemy.url"), target_metadata=target,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online():
    e = engine_from_config(cfg.get_section(cfg.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with e.connect() as conn:
        # batch mode bc sqlite cant ALTER most things
        context.configure(connection=conn, target_metadata=target, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
