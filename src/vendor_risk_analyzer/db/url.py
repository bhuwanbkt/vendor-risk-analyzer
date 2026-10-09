from __future__ import annotations

import os
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)


def normalize_database_url(
    database_url: str,
) -> str:
    """
    Normalize a PostgreSQL/Neon URL for
    SQLAlchemy + asyncpg operational tooling.
    """

    database_url = (
        database_url.strip()
    )

    if database_url.startswith(
        "postgres://"
    ):
        database_url = (
            database_url.replace(
                "postgres://",
                "postgresql://",
                1,
            )
        )

    if database_url.startswith(
        "postgresql+psycopg://"
    ):
        database_url = (
            database_url.replace(
                "postgresql+psycopg://",
                "postgresql://",
                1,
            )
        )

    if database_url.startswith(
        "postgresql+asyncpg://"
    ):
        database_url = (
            database_url.replace(
                "postgresql+asyncpg://",
                "postgresql://",
                1,
            )
        )

    parsed = urlsplit(
        database_url
    )

    query_params = dict(
        parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )
    )

    query_params.pop(
        "channel_binding",
        None,
    )

    sslmode = query_params.pop(
        "sslmode",
        None,
    )

    if (
        sslmode
        and "ssl"
        not in query_params
    ):
        query_params["ssl"] = (
            sslmode
        )

    query_params.setdefault(
        "prepared_statement_cache_size",
        "0",
    )

    return urlunsplit(
        (
            "postgresql+asyncpg",
            parsed.netloc,
            parsed.path,
            urlencode(
                query_params
            ),
            parsed.fragment,
        )
    )


def get_database_url() -> str:
    """
    Read DATABASE_URL without loading the full
    application settings object.

    Operational and smoke scripts only need the
    database connection, not auth/object-storage
    configuration.
    """

    database_url = os.getenv(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required."
        )

    return normalize_database_url(
        database_url
    )
