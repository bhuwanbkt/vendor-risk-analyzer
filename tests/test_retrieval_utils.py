from __future__ import annotations

from uuid import uuid4

import pytest

from vendor_risk_analyzer.retrieval.service import (
    normalize_uuid,
    vector_to_pgvector_literal,
)


def test_normalize_uuid_accepts_uuid_and_string() -> None:
    value = uuid4()

    assert normalize_uuid(
        value,
        field_name="vendor_id",
    ) == str(value)

    assert normalize_uuid(
        str(value),
        field_name="vendor_id",
    ) == str(value)


def test_normalize_uuid_rejects_invalid_value() -> None:
    with pytest.raises(
        ValueError,
        match="Invalid vendor_id",
    ):
        normalize_uuid(
            "not-a-uuid",
            field_name="vendor_id",
        )


def test_vector_to_pgvector_literal() -> None:
    result = vector_to_pgvector_literal(
        [
            0.1,
            -0.25,
            1.0,
        ]
    )

    assert result == "[0.1,-0.25,1]"


def test_iterative_scan_version_detection() -> None:
    from vendor_risk_analyzer.retrieval.service import version_at_least

    assert version_at_least("0.8.0", (0, 8))
    assert version_at_least("0.8.1", (0, 8))
    assert version_at_least("1.0", (0, 8))
    assert not version_at_least("0.7.4", (0, 8))
    assert not version_at_least(None, (0, 8))
    assert not version_at_least("", (0, 8))


def test_iterative_scan_is_enabled_only_when_supported(monkeypatch) -> None:
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from vendor_risk_analyzer.retrieval import service as retrieval

    def fake_db(version):
        statements = []

        @asynccontextmanager
        async def begin_nested():
            yield

        async def execute(statement, *args, **kwargs):
            statements.append(str(statement))
            return SimpleNamespace(scalar_one_or_none=lambda: version)

        return SimpleNamespace(execute=execute, begin_nested=begin_nested), statements

    for version, expected in (("0.8.0", True), ("0.7.4", False)):
        monkeypatch.setattr(retrieval, "_iterative_scan_supported", None)
        db, statements = fake_db(version)

        assert asyncio.run(retrieval.enable_iterative_scan(db)) is expected
        assert any("hnsw.iterative_scan" in s for s in statements) is expected

        # The version is read once per process.
        statements.clear()
        asyncio.run(retrieval.enable_iterative_scan(db))
        assert not any("pg_extension" in s for s in statements)
