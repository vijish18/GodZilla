"""No credentials in Alembic config; URLs arrive from environment or an injected connection."""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from godzilla.storage.models import Base

config = context.config


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = os.environ.get("GODZILLA_DATABASE_URL")
    if not url:
        raise RuntimeError("GODZILLA_DATABASE_URL is required")
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
