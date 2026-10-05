from dataclasses import (
    dataclass,
    field,
)
import re
from typing import Any

from vendor_risk_analyzer.ingestion.parsers.base import (
    ParsedElement,
)


# ==============================================================
# RETRIEVAL-EXCLUDED NAVIGATION SECTIONS
#
# We still preserve these sections in document_elements.
#
# We simply do not create retrieval chunks from their content.
#
# This prevents queries such as:
#
#   "What encryption controls does the vendor provide?"
#
# from retrieving:
#
#   Data Encryption ............ 5
#
# from a Table of Contents instead of the real substantive
# section.
# ==============================================================

NAVIGATION_SECTION_TITLES = {
    "table of contents",
    "contents",
    "list of figures",
    "list of tables",
}


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


# ==============================================================
# MAIN CHUNKING FUNCTION
# ==============================================================

def create_chunks(
    elements: list[ParsedElement],
    max_chars: int = 1200,
    overlap_elements: int = 1,
    long_text_overlap_chars: int = 160,
) -> list[ParsedChunk]:

    if max_chars <= 0:
        raise ValueError(
            "max_chars must be greater than 0"
        )

    if overlap_elements < 0:
        raise ValueError(
            "overlap_elements cannot be negative"
        )

    if long_text_overlap_chars < 0:
        raise ValueError(
            "long_text_overlap_chars cannot be negative"
        )

    chunks: list[
        ParsedChunk
    ] = []

    # ==========================================================
    # CURRENT NORMAL TEXT CHUNK
    # ==========================================================

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

        content = (
            "\n\n".join(
                current_parts
            )
            .strip()
        )

        if not content:
            return

        page_numbers = sorted(
            {
                page
                for page
                in current_pages
                if page is not None
            }
        )

        chunks.append(
            ParsedChunk(
                content=
                    content,

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
    # CLEAR CURRENT TEXT STATE
    # ==========================================================

    def clear_current() -> None:

        current_parts.clear()
        current_sequences.clear()
        current_pages.clear()

    # ==========================================================
    # PROCESS DOCUMENT ELEMENTS
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
        # Headings are used as retrieval metadata/context.
        #
        # We do not create standalone chunks containing only
        # headings.
        # ======================================================

        if (
            element.element_type
            == "heading"
        ):

            # Finish text belonging to the previous section.
            save_text_chunk()
            clear_current()

            # Keep current hierarchy even if this heading belongs
            # to a navigation section.
            #
            # That way following TOC paragraphs are recognized
            # as navigation content and skipped.
            current_heading_path[:] = (
                element.heading_path
            )

            continue

        # ======================================================
        # NAVIGATION CONTENT
        #
        # Examples:
        #
        # Table of Contents
        # Contents
        # List of Figures
        # List of Tables
        #
        # IMPORTANT:
        #
        # The elements remain stored in document_elements.
        #
        # We only prevent them from becoming retrieval chunks.
        # ======================================================

        if _is_navigation_element(
            element
        ):

            # A navigation block must never accidentally combine
            # with substantive content accumulated before it.
            save_text_chunk()
            clear_current()

            if element.heading_path:

                current_heading_path[:] = (
                    element.heading_path
                )

            continue

        # ======================================================
        # CAPTION
        #
        # Captions remain preserved in document_elements.
        #
        # Figure captions are not currently embedded by
        # themselves.
        #
        # Table captions are copied into table metadata by the
        # parser and prepended to the corresponding table chunk.
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
        # We intentionally do NOT force tables through the
        # 1200-character ordinary-text limit because arbitrary
        # splitting may destroy row/column relationships.
        #
        # Later, before embeddings, we can add semantic row-group
        # table splitting if real retrieval evaluation proves it
        # necessary.
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

            # --------------------------------------------------
            # Additional safety:
            #
            # Do not create table retrieval chunks if a table is
            # inside navigation material.
            # --------------------------------------------------

            if _is_navigation_heading_path(
                table_heading_path
            ):

                current_heading_path[:] = (
                    table_heading_path
                )

                continue

            caption = (
                element.metadata.get(
                    "caption"
                )
            )

            table_content = (
                element.content.strip()
            )

            # Include a real table caption in the retrieval text.
            #
            # Example:
            #
            # Table 1. CSF 2.0 Core Function...
            #
            # | Function | Category | ...
            #
            if caption:

                normalized_caption = (
                    str(
                        caption
                    )
                    .strip()
                )

                if normalized_caption:

                    table_content = (
                        f"{normalized_caption}"
                        "\n\n"
                        f"{table_content}"
                    )

            if not table_content:
                continue

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
        # NORMAL TEXT-LIKE ELEMENT
        #
        # paragraph
        # list_item
        # etc.
        # ======================================================

        text = (
            element
            .content
            .strip()
        )

        if not text:
            continue

        # Use the element's own hierarchy when available.
        if element.heading_path:

            current_heading_path[:] = (
                element.heading_path
            )

        # ------------------------------------------------------
        # Extra navigation guard.
        #
        # This also handles any future parser element type that
        # falls inside a navigation section.
        # ------------------------------------------------------

        if _is_navigation_heading_path(
            current_heading_path
        ):

            save_text_chunk()
            clear_current()

            continue

        # ======================================================
        # SINGLE SOURCE ELEMENT > max_chars
        #
        # Example:
        #
        # AWS Table of Contents previously contained a single
        # 1600+ character source element.
        #
        # Although TOCs are now excluded, this logic is still
        # necessary for legitimate oversized paragraphs in
        # arbitrary vendor documents.
        # ======================================================

        if (
            len(
                text
            )
            > max_chars
        ):

            # Save any previous normal chunk first.
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

                if not part:
                    continue

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

                            # Element overlap is not used because
                            # this chunk came from one source
                            # element.
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

        # Adding this element would exceed max_chars.
        if (
            current_parts

            and
            len(
                candidate
            )
            > max_chars
        ):

            # Store the current chunk.
            save_text_chunk()

            # --------------------------------------------------
            # Preserve element-level overlap.
            #
            # Example:
            #
            # Chunk A:
            #   P1
            #   P2
            #   P3
            #
            # Chunk B:
            #   P3
            #   P4
            #
            # if overlap_elements = 1.
            # --------------------------------------------------

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

            # Only keep the overlap if the overlap plus new text
            # itself still fits.
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

        # Add the new element.
        current_parts.append(
            text
        )

        current_sequences.append(
            sequence
        )

        current_pages.append(
            element.page_number
        )

    # ==========================================================
    # SAVE FINAL TEXT CHUNK
    # ==========================================================

    save_text_chunk()

    return chunks


# ==============================================================
# NAVIGATION SECTION DETECTION
# ==============================================================

def _is_navigation_element(
    element: ParsedElement,
) -> bool:
    """
    Return True when an element belongs to navigation-only
    document content.

    We check both:

    - heading_path
    - section_title

    because different parsers may populate structure slightly
    differently.

    The original element is NEVER deleted. This function only
    controls whether it becomes retrieval content.
    """

    if _is_navigation_heading_path(
        element.heading_path
    ):
        return True

    if element.section_title:

        normalized_section = (
            _normalize_section_title(
                element.section_title
            )
        )

        if (
            normalized_section
            in NAVIGATION_SECTION_TITLES
        ):
            return True

    return False


def _is_navigation_heading_path(
    heading_path: list[str],
) -> bool:
    """
    Check every level in a heading hierarchy.

    Examples:

        ["Table of Contents"]

        [
            "Framework (CSF) 2.0",
            "Table of Contents",
        ]

        [
            "Framework (CSF) 2.0",
            "List of Figures",
        ]

    All should be excluded from retrieval.
    """

    for heading in heading_path:

        normalized_heading = (
            _normalize_section_title(
                heading
            )
        )

        if (
            normalized_heading
            in NAVIGATION_SECTION_TITLES
        ):
            return True

    return False


def _normalize_section_title(
    value: str,
) -> str:
    """
    Normalize structural labels for reliable comparison.

    Examples:

        " Table   of Contents "
            ->
        "table of contents"

        "LIST OF FIGURES"
            ->
        "list of figures"
    """

    value = (
        str(
            value
        )
        .strip()
        .casefold()
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    # Remove simple trailing punctuation only.
    #
    # This allows:
    #
    #   Table of Contents:
    #
    # to match:
    #
    #   table of contents
    #
    value = value.rstrip(
        " :.-–—"
    )

    return value


# ==============================================================
# SPLIT ONE OVERSIZED TEXT ELEMENT
# ==============================================================

def _split_long_text(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    """
    Split a single source element that is larger than max_chars.

    Preference order for split boundaries:

    - paragraph boundary
    - newline
    - sentence ending
    - semicolon
    - comma
    - whitespace
    - hard character boundary

    A small character overlap is preserved between adjacent
    pieces.
    """

    text = (
        text.strip()
    )

    if not text:
        return []

    if max_chars <= 0:

        raise ValueError(
            "max_chars must be greater than 0"
        )

    # Keep overlap within a safe fraction of the chunk.
    overlap_chars = max(
        0,
        min(
            overlap_chars,
            max_chars // 3,
        ),
    )

    if (
        len(
            text
        )
        <= max_chars
    ):
        return [
            text
        ]

    pieces: list[
        str
    ] = []

    start = 0

    # We do not want to split extremely early in each window.
    #
    # Prefer a boundary in the final 40%.
    minimum_break_distance = max(
        1,
        int(
            max_chars
            * 0.60
        ),
    )

    while (
        start
        < len(
            text
        )
    ):

        hard_end = min(
            start
            + max_chars,

            len(
                text
            ),
        )

        # Last piece.
        if (
            hard_end
            >= len(
                text
            )
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

            # --------------------------------------------------
            # Look for a natural boundary.
            #
            # We gather available candidates and select the
            # latest safe boundary so the chunk remains close
            # to max_chars.
            # --------------------------------------------------

            for separator in (
                "\n\n",
                "\n",
                ". ",
                "? ",
                "! ",
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

            # Final defensive guarantee.
            #
            # This should normally already be <= max_chars.
            if (
                len(
                    piece
                )
                <= max_chars
            ):

                pieces.append(
                    piece
                )

            else:

                # Extremely defensive fallback.
                pieces.append(
                    piece[
                        :max_chars
                    ]
                    .strip()
                )

        if (
            end
            >= len(
                text
            )
        ):

            break

        # ------------------------------------------------------
        # CHARACTER OVERLAP
        # ------------------------------------------------------

        next_start = max(
            start + 1,
            end
            - overlap_chars,
        )

        # Try not to start inside a word.
        while (
            next_start
            < end

            and
            not text[
                next_start
            ].isspace()
        ):

            next_start += 1

        # Skip whitespace before the new piece.
        while (
            next_start
            < len(
                text
            )

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