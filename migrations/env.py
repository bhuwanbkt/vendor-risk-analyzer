from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from vendor_risk_analyzer.db.base import Base

# Import models so SQLAlchemy knows about all tables when Alembic
# uses Base.metadata.
from vendor_risk_analyzer.db import models  # noqa: F401


# ---------------------------------------------------------
# Alembic configuration
# ---------------------------------------------------------

config = context.config


# Configure Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# SQLAlchemy metadata used by Alembic
target_metadata = Base.metadata


# ---------------------------------------------------------
# Database URL
# ---------------------------------------------------------

def get_database_url() -> str:
    """
    Get DATABASE_URL directly from the environment.

    Alembic only needs the database connection string.
    It should NOT load the complete application Settings object,
    because migrations do not need:

    - ZITADEL configuration
    - session secrets
    - object-storage credentials
    - embedding settings
    - application-specific configuration

    Northflank provides DATABASE_URL to the migration job.
    """

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required to run database migrations."
        )

    return normalize_database_url(database_url)


def normalize_database_url(database_url: str) -> str:
    """
    Normalize a Neon PostgreSQL URL for SQLAlchemy + asyncpg.

    Neon URLs may look similar to:

        postgresql://user:password@host/database
            ?sslmode=require
            &channel_binding=require

    asyncpg does not need/support the channel_binding query
    parameter in this connection configuration.

    We also convert the URL to SQLAlchemy's asyncpg driver:

        postgresql+asyncpg://
    """

    database_url = database_url.strip()

    # -----------------------------------------------------
    # Normalize PostgreSQL scheme
    # -----------------------------------------------------

    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql://",
            1,
        )

    if database_url.startswith("postgresql+psycopg://"):
        database_url = database_url.replace(
            "postgresql+psycopg://",
            "postgresql://",
            1,
        )

    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )

    # -----------------------------------------------------
    # Parse URL
    # -----------------------------------------------------

    parsed = urlsplit(database_url)

    query_params = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    # -----------------------------------------------------
    # Neon-specific normalization
    # -----------------------------------------------------

    # asyncpg does not use channel_binding here.
    query_params.pop(
        "channel_binding",
        None,
    )

    # Neon commonly supplies:
    #
    #     sslmode=require
    #
    # asyncpg expects:
    #
    #     ssl=require
    #
    sslmode = query_params.pop(
        "sslmode",
        None,
    )

    if sslmode and "ssl" not in query_params:
        query_params["ssl"] = sslmode

    # Disable asyncpg prepared statement caching.
    #
    # This is useful with managed PostgreSQL / connection poolers
    # such as Neon.
    query_params.setdefault(
        "prepared_statement_cache_size",
        "0",
    )

    # -----------------------------------------------------
    # Build SQLAlchemy async URL
    # -----------------------------------------------------

    return urlunsplit(
        (
            "postgresql+asyncpg",
            parsed.netloc,
            parsed.path,
            urlencode(query_params),
            parsed.fragment,
        )
    )


# ---------------------------------------------------------
# Offline migrations
# ---------------------------------------------------------

def run_migrations_offline() -> None:
    """
    Run migrations without creating a live database connection.

    This is mainly useful when Alembic is generating SQL scripts.
    """

    database_url = get_database_url()

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------
# Online migrations
# ---------------------------------------------------------

def do_run_migrations(connection) -> None:
    """
    Execute Alembic migrations using an existing connection.
    """

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Create the async SQLAlchemy engine and execute migrations.
    """

    alembic_configuration = config.get_section(
        config.config_ini_section,
        {},
    )

    alembic_configuration[
        "sqlalchemy.url"
    ] = get_database_url()

    connectable = async_engine_from_config(
        alembic_configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(
                do_run_migrations
            )

    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    """
    Entry point for normal online migrations.
    """

    asyncio.run(
        run_async_migrations()
    )


# ---------------------------------------------------------
# Alembic entry point
# ---------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()