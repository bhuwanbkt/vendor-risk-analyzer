from __future__ import annotations

import pytest

from vendor_risk_analyzer.ingestion.chunker import (
    create_chunks,
)
from vendor_risk_analyzer.ingestion.parsers.base import (
    ParsedElement,
)


def test_empty_document_returns_no_chunks() -> None:
    assert create_chunks([]) == []


def test_simple_text_becomes_retrieval_chunk() -> None:
    elements = [
        ParsedElement(
            element_type="paragraph",
            content="Vendor encrypts customer data at rest.",
        )
    ]

    chunks = create_chunks(
        elements,
        max_chars=1200,
    )

    assert len(chunks) == 1
    assert (
        "Vendor encrypts customer data at rest."
        in chunks[0].content
    )
    assert (
        chunks[0].metadata["element_type"]
        == "text"
    )


def test_heading_is_context_not_standalone_chunk() -> None:
    elements = [
        ParsedElement(
            element_type="heading",
            content="Incident Response",
            heading_path=[
                "Incident Response"
            ],
        ),
        ParsedElement(
            element_type="paragraph",
            content=(
                "Customers are notified after "
                "confirmed material incidents."
            ),
        ),
    ]

    chunks = create_chunks(
        elements,
        max_chars=1200,
    )

    assert len(chunks) == 1
    assert chunks[0].metadata[
        "heading_path"
    ] == [
        "Incident Response"
    ]


def test_table_stays_standalone() -> None:
    elements = [
        ParsedElement(
            element_type="table",
            content=(
                "Control | Status\n"
                "Encryption | Implemented"
            ),
            metadata={
                "caption":
                    "Security Controls"
            },
        )
    ]

    chunks = create_chunks(elements)

    assert len(chunks) == 1
    assert (
        chunks[0].metadata["element_type"]
        == "table"
    )
    assert chunks[0].content.startswith(
        "Security Controls"
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"max_chars": 0},
            "max_chars",
        ),
        (
            {"overlap_elements": -1},
            "overlap_elements",
        ),
        (
            {
                "long_text_overlap_chars":
                    -1
            },
            "long_text_overlap_chars",
        ),
    ],
)
def test_invalid_chunking_parameters_raise(
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        create_chunks(
            [],
            **kwargs,
        )
