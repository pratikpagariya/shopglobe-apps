"""Alembic environment for catalog-svc.

Runs in two places with the SAME command:
  - in-cluster: the ArgoCD PreSync hook (charts/service/templates/migration-job.yaml)
  - locally:    the catalog-migrate one-shot in docker-compose.yml
DATABASE_URL comes from the environment via Settings, exactly as the service
reads it -- there is no URL in alembic.ini, because a URL holds a password.
"""

import asyncio
import logging
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from libs.common import Settings
from services.catalog_svc.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
log = logging.getLogger("alembic.env")

S = Settings(service_name="catalog-migrate")
target_metadata = Base.metadata

# The name is interpolated into CREATE DATABASE, which cannot take a bind
# parameter -- so it is validated instead.
_SAFE_DB_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


async def _ensure_database() -> None:
    """One RDS instance, one database per app env (shopglobe_dev / _stage /
    _prod), so the first migration in an env has to create its database.
    CREATE DATABASE cannot run inside a transaction (hence AUTOCOMMIT), and
    Postgres has no CREATE DATABASE IF NOT EXISTS (hence the lookup first)."""
    url = make_url(S.database_url)
    name = url.database or ""
    if not _SAFE_DB_NAME.match(name):
        raise RuntimeError(f"refusing to create a database named {name!r}")
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not found:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
                log.info("created database %s", name)
    finally:
        await admin.dispose()


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    await _ensure_database()
    engine = create_async_engine(S.database_url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_run_migrations)
    finally:
        await engine.dispose()


def run_offline() -> None:
    """`alembic upgrade head --sql`: print the SQL instead of running it -- what
    a reviewer reads before a risky migration reaches prod."""
    context.configure(url=S.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
