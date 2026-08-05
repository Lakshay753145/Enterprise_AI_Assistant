"""Alembic environment.

The database URL comes from backend.config.settings (i.e. the .env file), not
from alembic.ini, so credentials live in exactly one place.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Make the project root importable when alembic is invoked from anywhere.
PROJECT_ROOT = Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config.config import settings  # noqa: E402
from backend.database.database import Base  # noqa: E402
import backend.models  # noqa: E402,F401  (registers every table on Base.metadata)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Keep autogenerate from trying to manage objects we create by hand."""
    # The department-scoped kb_* views are managed by explicit SQL.
    if type_ == "table" and name.startswith("kb_"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The engine is built straight from settings rather than routed through
    # alembic.ini. alembic.ini is a configparser file with interpolation
    # enabled, so a URL-encoded password (e.g. '%40' for '@') would be read as
    # an interpolation token and fail with "invalid interpolation syntax".
    connectable = create_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()


