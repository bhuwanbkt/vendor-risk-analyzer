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
    long_text_overlap_chars: int = 160,
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

    current_pages: list[
        int | None
    ] = []

    current_heading_path: list[
        str
    ] = []

    # ==========================================================
    # SAVE CURRENT NORMAL TEXT CHUNK
    # ==========================================================

    def save_text_chunk() -> None:

        if not current_parts:
            return

        page_numbers = sorted(
            {
                page

                for page
                in current_pages

                if page
                is not None
            }
        )

        chunks.append(
            ParsedChunk(
                content=
                    "\n\n".join(
                        current_parts
                    ),

                source_sequences=
                    current_sequences.copy(),

                metadata={
                    "heading_path":
                        current_heading_path.copy(),

                    "element_type":
                        "text",

                    "overlap_elements":
                        overlap_elements,

                    "page_numbers":
                        page_numbers,
                },
            )
        )

    # ==========================================================
    # CLEAR CURRENT CHUNK
    # ==========================================================

    def clear_current() -> None:

        current_parts.clear()
        current_sequences.clear()
        current_pages.clear()

    # ==========================================================
    # PROCESS ELEMENTS
    # ==========================================================

    for (
        sequence,
        element,
    ) in enumerate(
        elements
    ):

        # ======================================================
        # HEADING
        #
        # Heading itself is metadata/context.
        # It is not embedded as a separate retrieval chunk.
        # ======================================================

        if (
            element.element_type
            == "heading"
        ):

            save_text_chunk()
            clear_current()

            current_heading_path[:] = (
                element.heading_path
            )

            continue

        # ======================================================
        # CAPTION
        #
        # Captions stay in document_elements.
        #
        # Table captions are also copied into table metadata by
        # PDFParser and are therefore included in table chunks.
        #
        # Figure captions are not useful enough by themselves
        # to become retrieval chunks.
        # ======================================================

        if (
            element.element_type
            == "caption"
        ):

            continue

        # ======================================================
        # TABLE
        #
        # Tables remain semantic units.
        #
        # They intentionally do NOT use the 1200-character text
        # limit because splitting arbitrary table rows can destroy
        # column relationships.
        # ======================================================

        if (
            element.element_type
            == "table"
        ):

            save_text_chunk()
            clear_current()

            table_heading_path = (
                element.heading_path.copy()

                if element.heading_path

                else
                current_heading_path.copy()
            )

            caption = (
                element.metadata.get(
                    "caption"
                )
            )

            table_content = (
                element.content
            )

            if caption:

                table_content = (
                    f"{caption}"
                    "\n\n"
                    f"{table_content}"
                )

            chunks.append(
                ParsedChunk(
                    content=
                        table_content,

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
        # NORMAL TEXT
        # ======================================================

        text = (
            element
            .content
            .strip()
        )

        if not text:
            continue

        if element.heading_path:

            current_heading_path[:] = (
                element.heading_path
            )

        # ======================================================
        # ONE SOURCE ELEMENT IS ITSELF > max_chars
        #
        # This fixes the AWS Table-of-Contents case where one
        # 1620-character element bypassed the old chunk limit.
        # ======================================================

        if (
            len(text)
            > max_chars
        ):

            save_text_chunk()
            clear_current()

            parts = (
                _split_long_text(
                    text,

                    max_chars=
                        max_chars,

                    overlap_chars=
                        long_text_overlap_chars,
                )
            )

            part_count = len(
                parts
            )

            for (
                part_index,
                part,
            ) in enumerate(
                parts,
                start=1,
            ):

                chunks.append(
                    ParsedChunk(
                        content=
                            part,

                        source_sequences=[
                            sequence
                        ],

                        metadata={
                            "heading_path":
                                current_heading_path.copy(),

                            "element_type":
                                "text",

                            "overlap_elements":
                                0,

                            "character_overlap":
                                long_text_overlap_chars,

                            "split_from_single_element":
                                True,

                            "split_part":
                                part_index,

                            "split_part_count":
                                part_count,

                            "page_numbers":
                                (
                                    [
                                        element.page_number
                                    ]

                                    if (
                                        element.page_number
                                        is not None
                                    )

                                    else []
                                ),
                        },
                    )
                )

            continue

        # ======================================================
        # NORMAL MULTI-ELEMENT CHUNKING
        # ======================================================

        candidate = (
            "\n\n".join(
                current_parts
                + [text]
            )
        )

        if (
            current_parts
            and
            len(candidate)
            > max_chars
        ):

            save_text_chunk()

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

            overlap_pages = (
                current_pages[
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

                and
                len(
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

                current_pages[:] = (
                    overlap_pages
                )

            else:

                clear_current()

        current_parts.append(
            text
        )

        current_sequences.append(
            sequence
        )

        current_pages.append(
            element.page_number
        )

    save_text_chunk()

    return chunks


# ==============================================================
# SPLIT ONE OVERSIZED TEXT ELEMENT
# ==============================================================

def _split_long_text(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:

    text = text.strip()

    if not text:
        return []

    if max_chars <= 0:

        raise ValueError(
            "max_chars must be greater than 0"
        )

    # Keep the overlap reasonable even if someone supplies a
    # very large overlap value.
    overlap_chars = max(
        0,
        min(
            overlap_chars,
            max_chars // 3,
        ),
    )

    if len(text) <= max_chars:
        return [
            text
        ]

    pieces: list[
        str
    ] = []

    start = 0

    # Prefer a natural break in the final 40% of the window.
    minimum_break_distance = max(
        1,
        int(
            max_chars
            * 0.60
        ),
    )

    while start < len(text):

        hard_end = min(
            start + max_chars,
            len(text),
        )

        if (
            hard_end
            >= len(text)
        ):

            end = len(
                text
            )

        else:

            search_start = min(
                start
                + minimum_break_distance,

                hard_end,
            )

            break_positions: list[
                int
            ] = []

            # Stronger boundaries first conceptually, but we
            # choose the latest safe break so chunks remain close
            # to max_chars.
            for separator in (
                "\n\n",
                "\n",
                ". ",
                "; ",
                ", ",
                " ",
            ):

                position = (
                    text.rfind(
                        separator,
                        search_start,
                        hard_end,
                    )
                )

                if (
                    position
                    != -1
                ):

                    break_positions.append(
                        position
                        + len(
                            separator
                        )
                    )

            end = (
                max(
                    break_positions
                )

                if break_positions

                else hard_end
            )

        piece = (
            text[
                start:end
            ]
            .strip()
        )

        if piece:

            pieces.append(
                piece
            )

        if (
            end
            >= len(text)
        ):

            break

        # Character overlap for a single large source element.
        next_start = max(
            start + 1,
            end
            - overlap_chars,
        )

        # Avoid starting in the middle of a word.
        while (
            next_start
            < end

            and
            not text[
                next_start
            ].isspace()
        ):

            next_start += 1

        while (
            next_start
            < len(text)

            and
            text[
                next_start
            ].isspace()
        ):

            next_start += 1

        # Infinite-loop protection.
        if (
            next_start
            <= start
        ):

            next_start = (
                end
            )

        start = (
            next_start
        )

    return pieces