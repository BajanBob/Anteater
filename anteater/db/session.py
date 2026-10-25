"""Engine and session factory, plus schema creation/upgrade."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from anteater.db.models import Base

log = logging.getLogger(__name__)


def make_engine(url: str, echo: bool = False) -> AsyncEngine:
    if url.startswith("sqlite"):
        # make sure the directory for a file database exists
        path = url.split(":///", 1)[-1]
        if path and path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        return create_async_engine(url, echo=echo, connect_args={"timeout": 30})
    return create_async_engine(url, echo=echo, pool_pre_ping=True, pool_size=5, max_overflow=5)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create tables directly (SQLite, tests, frozen builds without alembic scripts)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.execute(text("PRAGMA journal_mode=WAL"))
        elif engine.dialect.name == "postgresql":
            await make_hypertable(conn)


async def make_hypertable(conn) -> None:  # type: ignore[no-untyped-def]
    """Turn observations into a TimescaleDB hypertable when the extension is there."""
    try:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await conn.execute(
            text("SELECT create_hypertable('observations', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")
        )
    except Exception as exc:  # plain PostgreSQL works too, just without compression/retention
        log.info("TimescaleDB not available (%s); using a plain table", exc.__class__.__name__)


def upgrade(url: str) -> str:
    """Run alembic migrations to head. Falls back to create_all if scripts aren't bundled.

    Returns a short description of what was done.
    """
    import asyncio

    try:
        from alembic import command
        from alembic.config import Config

        from anteater.paths import data_dir

        script_dir = data_dir("alembic")
    except (ImportError, FileNotFoundError):
        asyncio.run(_create(url))
        return "schema created (alembic scripts not found, used create_all)"

    cfg = Config()
    cfg.set_main_option("script_location", str(script_dir))
    # Config values go through configparser interpolation; escape % (e.g. in passwords)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # `collect`, `detect` and `ui` create tables with create_all, which leaves no
    # alembic_version row. Mark such a database as current instead of re-creating tables.
    if asyncio.run(_unversioned_schema(url)):
        command.stamp(cfg, "head")
        command.upgrade(cfg, "head")
        return "existing schema stamped and migrated to head"
    command.upgrade(cfg, "head")
    return "migrated to head"


async def _unversioned_schema(url: str) -> bool:
    """True when the tables exist but alembic has never recorded a version."""
    from sqlalchemy import inspect

    engine = make_engine(url)
    try:
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    return "observations" in names and "alembic_version" not in names


async def _create(url: str) -> None:
    engine = make_engine(url)
    try:
        await create_all(engine)
    finally:
        await engine.dispose()
