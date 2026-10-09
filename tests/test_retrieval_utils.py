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
