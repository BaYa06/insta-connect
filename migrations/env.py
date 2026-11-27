from alembic import context
from sqlalchemy import create_engine

from flashly.config import get_settings
from flashly.db import Base

target_metadata = Base.metadata


def _url() -> str:
    s = get_settings()
    if not s.database_url:
        raise SystemExit("DATABASE_URL is not set")
    return s.database_url.get_secret_value()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
