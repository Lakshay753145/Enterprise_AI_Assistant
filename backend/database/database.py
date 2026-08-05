"""Database engines and session factories.

Three connections, deliberately separate:

* **async engine** - everything the API does. psycopg3 async driver.
* **sync engine** - alembic, CLI ingestion scripts, and anything that cannot
  be async.
* **SQL-agent engine** - a *read-only* Postgres role that can only see
  department-scoped views. The LLM-driven SQL agent gets this one and nothing
  else, so even a perfect prompt injection cannot write, drop, or read across
  departments. See scripts/setup_db_roles.sql.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from backend.config.config import settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


# ---------------------------------------------------------------------------
# Async engine - the API's workhorse
# ---------------------------------------------------------------------------
async_engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # survive Postgres restarts / idle connection reaping
    pool_recycle=1800,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # keep objects usable after commit in request scope
)


# ---------------------------------------------------------------------------
# Sync engine - alembic, ingestion CLI, admin scripts
# ---------------------------------------------------------------------------
_sync_engine = None


def get_sync_engine():
    """Build (once) and return the synchronous engine."""
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            pool_pre_ping=True,
            future=True,
        )
        event.listen(_sync_engine, "connect", _register_vector_on_connect)
    return _sync_engine


def get_sync_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_sync_engine(), autocommit=False, autoflush=False)


# ---------------------------------------------------------------------------
# Read-only engine for the SQL agent
# ---------------------------------------------------------------------------
_readonly_engine = None


def get_readonly_engine():
    """Engine bound to the restricted `ai_readonly` role.

    NullPool because the SQL agent is low-volume and we would rather not hold
    idle connections open. `default_transaction_read_only` is forced at the
    connection level, so no generated SQL can mutate anything even if the
    role's grants were ever loosened by mistake.
    """
    global _readonly_engine
    if _readonly_engine is None:
        url = settings.SQL_AGENT_DATABASE_URL or settings.DATABASE_URL
        _readonly_engine = create_engine(
            url,
            echo=False,
            poolclass=NullPool,
            future=True,
            connect_args={"options": "-c default_transaction_read_only=on"},
        )
    return _readonly_engine


# ---------------------------------------------------------------------------
# Row-Level Security plumbing
# ---------------------------------------------------------------------------
# RLS policies (see the alembic migration) key off two session-local GUCs.
# Setting them is how a request tells Postgres who it is. They are set with
# set_config(..., true) so they are transaction-scoped and cannot leak to the
# next request that happens to reuse the same pooled connection.

async def apply_rls_context(
    session: AsyncSession,
    *,
    department: str,
    role: str,
) -> None:
    """Bind the current transaction to a department + role.

    Must be called before any query against an RLS-protected table. The
    department filter in application code is the first line of defence; this is
    the second, enforced by Postgres itself.
    """
    await session.execute(
        text("SELECT set_config('app.current_department', :dept, true)"),
        {"dept": department},
    )
    await session.execute(
        text("SELECT set_config('app.current_role', :role, true)"),
        {"role": role},
    )


def apply_rls_context_sync(session: Session, *, department: str, role: str) -> None:
    """Synchronous twin of :func:`apply_rls_context`, for CLI ingestion."""
    session.execute(
        text("SELECT set_config('app.current_department', :dept, true)"),
        {"dept": department},
    )
    session.execute(
        text("SELECT set_config('app.current_role', :role, true)"),
        {"role": role},
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session; roll back and close on any error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def get_sync_db() -> Generator[Session, None, None]:
    """Synchronous session for scripts. Not used by request handlers."""
    factory = get_sync_session_factory()
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# pgvector registration
# ---------------------------------------------------------------------------
# psycopg3 needs the vector type registered on each new raw connection so it
# can adapt Python lists to `vector` and back.

def _register_vector_on_connect(dbapi_connection: Any, _record: Any) -> None:
    try:
        from pgvector.psycopg import register_vector

        register_vector(dbapi_connection)
    except Exception:  # pragma: no cover - extension not yet installed
        pass


event.listen(async_engine.sync_engine, "connect", _register_vector_on_connect)


# ---------------------------------------------------------------------------
# Health / lifecycle
# ---------------------------------------------------------------------------

async def check_database_health() -> dict[str, Any]:
    """Ping the DB and confirm the pgvector extension is present."""
    from backend.core.logging_config import app_logger

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            result = await session.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            version = result.scalar_one_or_none()

        return {"status": "healthy", "pgvector": version or "not installed"}
    except Exception as exc:
        app_logger.error(f"Database health check failed: {exc}")
        return {"status": "unhealthy", "error": str(exc)}


async def dispose_engines() -> None:
    """Close pools on shutdown."""
    await async_engine.dispose()
    if _sync_engine is not None:
        _sync_engine.dispose()
    if _readonly_engine is not None:
        _readonly_engine.dispose()
