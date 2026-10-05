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
) -> list[ParsedChunk]:

    chunks: list[ParsedChunk] = []

    current_parts: list[str] = []
    current_sequences: list[int] = []
    current_heading_path: list[str] = []

    def flush() -> None:
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
                },
            )
        )

        current_parts.clear()
        current_sequences.clear()

    for sequence, element in enumerate(
        elements
    ):
        if element.element_type == "heading":
            flush()

            current_heading_path[:] = (
                element.heading_path
            )

            continue

        text = element.content.strip()

        if not text:
            continue

        candidate = "\n\n".join(
            current_parts + [text]
        )

        if (
            current_parts
            and len(candidate) > max_chars
        ):
            flush()

        current_parts.append(text)
        current_sequences.append(sequence)

        if element.heading_path:
            current_heading_path[:] = (
                element.heading_path
            )

    flush()

    return chunks