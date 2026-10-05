from dataclasses import dataclass, field
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

    chunks: list[ParsedChunk] = []

    current_parts: list[str] = []
    current_sequences: list[int] = []

    current_heading_path: list[str] = []


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
                },
            )
        )


    for sequence, element in enumerate(
        elements
    ):

        # ----------------------------------------------------
        # Heading boundary
        # ----------------------------------------------------

        if element.element_type == "heading":

            # Do not overlap content across unrelated sections.
            save_chunk()

            current_parts.clear()
            current_sequences.clear()

            current_heading_path[:] = (
                element.heading_path
            )

            continue


        text = element.content.strip()

        if not text:
            continue


        # ----------------------------------------------------
        # Check whether adding this element exceeds chunk size
        # ----------------------------------------------------

        candidate = "\n\n".join(
            current_parts + [text]
        )


        if (
            current_parts
            and len(candidate) > max_chars
        ):

            # Save the completed chunk.
            save_chunk()


            # -----------------------------------------------
            # Preserve the last N source elements
            # as overlap for the next chunk.
            # -----------------------------------------------

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


            # Make sure overlap + new element
            # still fits reasonably within max_chars.
            overlap_candidate = "\n\n".join(
                overlap_parts + [text]
            )


            if (
                overlap_parts
                and len(overlap_candidate)
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


        # ----------------------------------------------------
        # Add current source element
        # ----------------------------------------------------

        current_parts.append(text)

        current_sequences.append(
            sequence
        )


        if element.heading_path:
            current_heading_path[:] = (
                element.heading_path
            )


    # Save final chunk.
    save_chunk()

    return chunks