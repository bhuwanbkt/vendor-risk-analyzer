from __future__ import annotations

import pytest

from vendor_risk_analyzer.db.url import (
    get_database_url,
    normalize_database_url,
)


def test_normalize_neon_database_url() -> None:
    result = normalize_database_url(
        "postgresql://user:pass@example.com/db"
        "?sslmode=require"
        "&channel_binding=require"
    )

    assert result.startswith(
        "postgresql+asyncpg://"
    )
    assert "ssl=require" in result
    assert "channel_binding" not in result
    assert (
        "prepared_statement_cache_size=0"
        in result
    )


def test_normalize_asyncpg_url_is_idempotent() -> None:
    result = normalize_database_url(
        "postgresql+asyncpg://user:pass@"
        "example.com/db?ssl=require"
        "&prepared_statement_cache_size=0"
    )

    assert result.count(
        "postgresql+asyncpg://"
    ) == 1
    assert "ssl=require" in result


def test_get_database_url_requires_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        "DATABASE_URL",
        raising=False,
    )

    with pytest.raises(
        RuntimeError,
        match="DATABASE_URL is required",
    ):
        get_database_url()
