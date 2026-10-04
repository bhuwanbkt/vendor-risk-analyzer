from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vendor_risk_analyzer.config import get_settings


settings = get_settings()


def build_async_database_url(raw_url: str):
    url = make_url(raw_url)

    query = dict(url.query)

    # Neon URLs commonly provide sslmode.
    # asyncpg expects ssl instead.
    if query.pop("sslmode", None):
        query["ssl"] = "require"

    # asyncpg does not need this PostgreSQL connection option.
    query.pop("channel_binding", None)

    # Safer when using a pooled Neon connection.
    query["prepared_statement_cache_size"] = "0"

    return url.set(
        drivername="postgresql+asyncpg",
        query=query,
    )


database_url = build_async_database_url(
    settings.database_url
)


engine = create_async_engine(
    database_url,
    pool_size=2,
    max_overflow=1,
    pool_pre_ping=True,
    pool_recycle=300,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))