from __future__ import annotations

from vendor_risk_analyzer.embeddings.repository import (
    build_embedding_title,
    normalize_metadata,
    vector_to_pgvector_literal,
)


def test_normalize_metadata_accepts_dict() -> None:
    value = {
        "heading_path": [
            "Security",
            "Encryption",
        ]
    }

    assert normalize_metadata(
        value
    ) == value


def test_normalize_metadata_parses_json_object() -> None:
    assert normalize_metadata(
        '{"sheet_name": "Controls"}'
    ) == {
        "sheet_name": "Controls"
    }


def test_normalize_metadata_rejects_non_object_json() -> None:
    assert normalize_metadata(
        '["not", "an", "object"]'
    ) == {}


def test_embedding_title_prefers_heading_path() -> None:
    result = build_embedding_title(
        {
            "heading_path": [
                "Security",
                "Incident Response",
            ],
            "caption": "Ignored caption",
        }
    )

    assert (
        result
        == "Security > Incident Response"
    )


def test_embedding_title_falls_back_to_structural_label() -> None:
    assert build_embedding_title(
        {
            "heading_path": [],
            "table_title":
                "Control Matrix",
        }
    ) == "Control Matrix"


def test_vector_literal_is_pgvector_compatible() -> None:
    assert vector_to_pgvector_literal(
        [
            0.1,
            -0.25,
            1.0,
        ]
    ) == "[0.1,-0.25,1]"
