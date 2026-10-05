from dataclasses import (
    dataclass,
    field,
)
from typing import Any

from vendor_risk_analyzer.ingestion.parsers.base import (
    ParsedElement,
)


@dataclass(slots=True)
class ParsedChunk:
    content: str

    source_sequences: list[int]

    source_element_ids: list[str] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


def create_chunks(
    elements: list[ParsedElement],
    max_chars: int = 1200,
    overlap_elements: int = 1,
) -> list[ParsedChunk]:

    chunks: list[
        ParsedChunk
    ] = []

    current_parts: list[
        str
    ] = []

    current_sequences: list[
        int
    ] = []

    current_heading_path: list[
        str
    ] = []

    # ==========================================================
    # SAVE CURRENT TEXT CHUNK
    # ==========================================================

    def save_chunk() -> None:

        if not current_parts:
            return

        chunks.append(
            ParsedChunk(
                content="\n\n".join(
                    current_parts
                ),

                source_sequences=(
                    current_sequences.copy()
                ),

                metadata={
                    "heading_path":
                        current_heading_path.copy(),

                    "overlap_elements":
                        overlap_elements,

                    "element_type":
                        "text",
                },
            )
        )

    # ==========================================================
    # PROCESS ELEMENTS
    # ==========================================================

    for sequence, element in enumerate(
        elements
    ):

        # ======================================================
        # HEADING
        #
        # Close existing chunk.
        # Don't overlap across section boundaries.
        # ======================================================

        if (
            element.element_type
            == "heading"
        ):
            save_chunk()

            current_parts.clear()
            current_sequences.clear()

            current_heading_path[:] = (
                element.heading_path
            )

            continue

        # ======================================================
        # TABLE
        #
        # Tables become standalone semantic chunks.
        #
        # We do not overlap paragraph text into/out of tables.
        # ======================================================

        if (
            element.element_type
            == "table"
        ):
            save_chunk()

            current_parts.clear()
            current_sequences.clear()

            table_heading_path = (
                element.heading_path.copy()
                if element.heading_path
                else
                current_heading_path.copy()
            )

            chunks.append(
                ParsedChunk(
                    content=
                        element.content,

                    source_sequences=[
                        sequence
                    ],

                    metadata={
                        "heading_path":
                            table_heading_path,

                        "element_type":
                            "table",

                        "overlap_elements":
                            0,

                        "page_number":
                            element.page_number,

                        **element.metadata,
                    },
                )
            )

            current_heading_path[:] = (
                table_heading_path
            )

            continue

        # ======================================================
        # NORMAL TEXT / PARAGRAPH
        # ======================================================

        text = (
            element.content
            .strip()
        )

        if not text:
            continue

        candidate = "\n\n".join(
            current_parts
            + [text]
        )

        # ======================================================
        # CHUNK LIMIT REACHED
        # ======================================================

        if (
            current_parts
            and len(candidate)
            > max_chars
        ):
            save_chunk()

            overlap_parts = (
                current_parts[
                    -overlap_elements:
                ]
                if overlap_elements > 0
                else []
            )

            overlap_sequences = (
                current_sequences[
                    -overlap_elements:
                ]
                if overlap_elements > 0
                else []
            )

            overlap_candidate = (
                "\n\n".join(
                    overlap_parts
                    + [text]
                )
            )

            if (
                overlap_parts
                and len(
                    overlap_candidate
                )
                <= max_chars
            ):
                current_parts[:] = (
                    overlap_parts
                )

                current_sequences[:] = (
                    overlap_sequences
                )

            else:
                current_parts.clear()
                current_sequences.clear()

        current_parts.append(
            text
        )

        current_sequences.append(
            sequence
        )

        if element.heading_path:
            current_heading_path[:] = (
                element.heading_path
            )

    # ==========================================================
    # FINAL CHUNK
    # ==========================================================

    save_chunk()

    return chunks