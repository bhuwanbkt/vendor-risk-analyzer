from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any

from vendor_risk_analyzer.ingestion.parsers.base import (
    ParsedElement,
)


# ==============================================================
# STRONG NAVIGATION HEADING HINTS
#
# These headings strongly suggest that neighboring content may
# be navigation.
#
# IMPORTANT:
#
# They do NOT automatically cause all child elements to be
# excluded from retrieval.
#
# Each child element is still evaluated independently.
# ==============================================================

STRONG_NAVIGATION_TITLES = {
    "table of contents",
    "contents",
    "list of figures",
    "list of tables",
    "list of illustrations",
    "index",
    "document index",
    "section index",
}


# ==============================================================
# NAVIGATION CONTENT PATTERNS
# ==============================================================


# Examples:
#
# Security Controls .................... 12
# Appendix A ........................... 24
# Preface .............................. iv
#
DOT_LEADER_PAGE_REFERENCE_PATTERN = re.compile(
    r"\.{3,}"
    r"\s*"
    r"(?:\d+|[ivxlcdm]+)"
    r"\b",
    re.IGNORECASE,
)


# Examples:
#
# 1 Introduction 3
# 2 Security Architecture 5
# 2.1 Encryption 7
#
NUMBERED_PAGE_REFERENCE_PATTERN = re.compile(
    r"^"
    r"\d+"
    r"(?:\.\d+)*"
    r"\.?"
    r"\s+"
    r".{1,150}?"
    r"\s+"
    r"(?:\d+|[ivxlcdm]+)"
    r"$",
    re.IGNORECASE,
)


# Examples:
#
# Figure 1. Architecture 6
# Fig. 2. Data Flow 9
# Table 3. Subprocessors 17
#
FIGURE_TABLE_PAGE_REFERENCE_PATTERN = re.compile(
    r"^"
    r"(?:fig(?:ure)?\.?|table)"
    r"\s+"
    r"[A-Za-z]?\d+"
    r"(?:[.:\-–—])?"
    r"\s+"
    r".{1,150}?"
    r"\s+"
    r"(?:\d+|[ivxlcdm]+)"
    r"$",
    re.IGNORECASE,
)


# Weak signal:
#
# Security Architecture 8
# Preface iv
#
# Never trusted by itself.
#
SHORT_TITLE_PAGE_REFERENCE_PATTERN = re.compile(
    r"^"
    r"[A-Za-z]"
    r".{1,120}?"
    r"\s+"
    r"(?:\d+|[ivxlcdm]+)"
    r"$",
    re.IGNORECASE,
)


# ==============================================================
# CHUNK MODEL
# ==============================================================


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

    # ==========================================================
    # Analyze navigation before chunking.
    #
    # Returns:
    #
    # {
    #     element_sequence: exclusion_reason
    # }
    #
    # Only the individual navigation-looking child elements are
    # excluded.
    # ==========================================================

    navigation_exclusions = (
        _detect_navigation_elements(
            elements
        )
    )

    chunks: list[ParsedChunk] = []

    current_parts: list[str] = []

    current_sequences: list[int] = []

    current_pages: list[int | None] = []

    current_heading_path: list[str] = []

    # ==========================================================
    # SAVE CURRENT NORMAL TEXT CHUNK
    # ==============================================================

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
                content=content,

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
    # ==============================================================

    def clear_current() -> None:

        current_parts.clear()
        current_sequences.clear()
        current_pages.clear()

    # ==========================================================
    # PROCESS ELEMENTS
    # ==============================================================

    for sequence, element in enumerate(
        elements
    ):

        # ======================================================
        # HEADING
        #
        # Headings provide structural context.
        # They do not become standalone retrieval chunks.
        # ======================================================

        if element.element_type == "heading":

            save_text_chunk()
            clear_current()

            current_heading_path[:] = (
                element.heading_path
            )

            continue

        # ======================================================
        # NAVIGATION CHILD ELEMENT
        #
        # The original element remains in document_elements.
        #
        # Only retrieval chunk creation is skipped.
        # ======================================================

        if sequence in navigation_exclusions:

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
        # Figure captions stay in document_elements.
        #
        # Table captions are attached to the table metadata by
        # the parser and later prepended to the table chunk.
        # ======================================================

        if element.element_type == "caption":
            continue

        # ======================================================
        # TABLE
        # ======================================================

        if element.element_type == "table":

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
                element.content.strip()
            )

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
        # SINGLE SOURCE ELEMENT > max_chars
        # ======================================================

        if len(text) > max_chars:

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

            for part_index, part in enumerate(
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
            len(candidate) > max_chars
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
# NAVIGATION DETECTION
# ==============================================================


def _detect_navigation_elements(
    elements: list[ParsedElement],
) -> dict[int, str]:
    """
    Detect navigation at the CHILD ELEMENT level.

    Important behavior:

        Table of Contents
            Security ............ 4
            Encryption .......... 7

            This document is historical.

    Results:

        Security ............ 4
            -> excluded

        Encryption .......... 7
            -> excluded

        This document is historical.
            -> preserved

    A navigation-looking heading is only a context signal.
    """

    groups: dict[
        tuple[str, ...],
        list[
            tuple[
                int,
                ParsedElement,
            ]
        ],
    ] = defaultdict(
        list
    )

    # ==========================================================
    # GROUP BY STRUCTURAL CONTEXT
    # ======================================================

    for sequence, element in enumerate(
        elements
    ):

        key = (
            _navigation_context_key(
                element
            )
        )

        groups[
            key
        ].append(
            (
                sequence,
                element,
            )
        )

    exclusions: dict[
        int,
        str
    ] = {}

    # ==========================================================
    # INSPECT EACH GROUP
    # ======================================================

    for context_key, members in (
        groups.items()
    ):

        strong_navigation_context = (
            _group_has_strong_navigation_title(
                members
            )
        )

        content_members = [
            (
                sequence,
                element,
            )

            for sequence, element
            in members

            if (
                element.element_type
                != "heading"

                and
                element.content.strip()
            )
        ]

        if not content_members:
            continue

        scores = {
            sequence:
                _navigation_evidence_score(
                    element.content
                )

            for sequence, element
            in content_members
        }

        # ======================================================
        # GROUP DENSITY
        #
        # Used only as supporting evidence.
        #
        # It never means:
        #
        #   exclude every child.
        # ======================================================

        medium_count = sum(
            1

            for score
            in scores.values()

            if score >= 0.60
        )

        strong_count = sum(
            1

            for score
            in scores.values()

            if score >= 0.85
        )

        content_count = len(
            content_members
        )

        medium_density = (
            medium_count
            /
            content_count
        )

        # ======================================================
        # CLASSIFY CHILDREN INDIVIDUALLY
        # ======================================================

        for sequence, element in (
            content_members
        ):

            score = scores[
                sequence
            ]

            text = (
                element.content
            )

            # --------------------------------------------------
            # CASE 1:
            #
            # Extremely strong navigation evidence.
            #
            # Example:
            #
            # Encryption ........ 7
            # --------------------------------------------------

            if score >= 0.90:

                exclusions[
                    sequence
                ] = (
                    "strong_navigation_pattern"
                )

                continue

            # --------------------------------------------------
            # CASE 2:
            #
            # Numbered / figure / table reference.
            #
            # Example:
            #
            # 2.1 Encryption 7
            #
            # The score is high enough that it can stand alone.
            # --------------------------------------------------

            if score >= 0.85:

                exclusions[
                    sequence
                ] = (
                    "navigation_reference"
                )

                continue

            # --------------------------------------------------
            # CASE 3:
            #
            # Weak navigation-looking element, but the parent
            # heading is explicitly navigation.
            #
            # Example:
            #
            # Table of Contents
            #
            #   Security Architecture 8
            #
            # --------------------------------------------------

            if (
                strong_navigation_context
                and
                score >= 0.60
            ):

                exclusions[
                    sequence
                ] = (
                    "navigation_heading_and_pattern"
                )

                continue

            # --------------------------------------------------
            # CASE 4:
            #
            # Unknown heading but navigation-dense siblings.
            #
            # Example:
            #
            # Overview
            #
            #   Security 5
            #   Privacy 9
            #   Compliance 12
            #
            # if most entries resemble page references.
            # --------------------------------------------------

            if (
                content_count >= 3
                and
                medium_density >= 0.70
                and
                score >= 0.60
            ):

                exclusions[
                    sequence
                ] = (
                    "navigation_pattern_density"
                )

                continue

            # --------------------------------------------------
            # CASE 5:
            #
            # One large PDF block may contain many TOC entries.
            #
            # Example:
            #
            # Security ........ 5
            # Privacy ......... 8
            # Compliance ..... 11
            #
            # --------------------------------------------------

            if (
                _count_navigation_references(
                    text
                )
                >= 3

                and
                score >= 0.85
            ):

                exclusions[
                    sequence
                ] = (
                    "navigation_reference_block"
                )

                continue

            # Otherwise preserve it.

    return exclusions


# ==============================================================
# STRONG NAVIGATION CONTEXT
# ==============================================================


def _group_has_strong_navigation_title(
    members: list[
        tuple[
            int,
            ParsedElement,
        ]
    ],
) -> bool:

    for _, element in members:

        for heading in (
            element.heading_path
        ):

            if (
                _is_strong_navigation_title(
                    heading
                )
            ):
                return True

        if (
            element.section_title
            and
            _is_strong_navigation_title(
                element.section_title
            )
        ):

            return True

    return False


# ==============================================================
# NAVIGATION GROUPING KEY
# ==============================================================


def _navigation_context_key(
    element: ParsedElement,
) -> tuple[str, ...]:

    if element.heading_path:

        return (
            "heading_path",
            *[
                _normalize_section_title(
                    heading
                )

                for heading
                in element.heading_path

                if heading
            ],
        )

    if element.section_title:

        return (
            "section",
            _normalize_section_title(
                element.section_title
            ),
        )

    if (
        element.page_number
        is not None
    ):

        return (
            "page",
            str(
                element.page_number
            ),
        )

    return (
        "unscoped",
    )


# ==============================================================
# STRONG NAVIGATION TITLE
# ==============================================================


def _is_strong_navigation_title(
    title: str,
) -> bool:

    normalized = (
        _normalize_section_title(
            title
        )
    )

    if (
        normalized
        in STRONG_NAVIGATION_TITLES
    ):
        return True

    # Support:
    #
    # Table of Contents - Security Policy
    # Contents: Vendor Handbook
    #
    # without broadly matching arbitrary prose.
    for navigation_title in (
        STRONG_NAVIGATION_TITLES
    ):

        if (
            normalized.startswith(
                navigation_title
                + " - "
            )

            or
            normalized.startswith(
                navigation_title
                + " — "
            )

            or
            normalized.startswith(
                navigation_title
                + ": "
            )
        ):

            return True

    return False


# ==============================================================
# NAVIGATION EVIDENCE SCORE
# ==============================================================


def _navigation_evidence_score(
    text: str,
) -> float:

    normalized = (
        _normalize_content_text(
            text
        )
    )

    if not normalized:
        return 0.0

    # ==========================================================
    # DOT LEADERS
    #
    # Very strong signal.
    # ======================================================

    dot_reference_count = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN
        .findall(
            normalized
        )
    )

    if dot_reference_count >= 2:
        return 1.0

    if dot_reference_count == 1:
        return 0.95

    # ==========================================================
    # FIGURE / TABLE INDEX ENTRY
    # ======================================================

    if (
        FIGURE_TABLE_PAGE_REFERENCE_PATTERN
        .fullmatch(
            normalized
        )
    ):

        return 0.95

    # ==========================================================
    # NUMBERED TOC ENTRY
    # ======================================================

    if (
        len(normalized) <= 180

        and
        NUMBERED_PAGE_REFERENCE_PATTERN
        .fullmatch(
            normalized
        )
    ):

        return 0.85

    # ==========================================================
    # WEAK SHORT TITLE + PAGE NUMBER
    # ======================================================

    word_count = len(
        normalized.split()
    )

    if (
        len(normalized) <= 120

        and
        word_count <= 14

        and
        SHORT_TITLE_PAGE_REFERENCE_PATTERN
        .fullmatch(
            normalized
        )

        and
        not normalized.endswith(
            (
                ".",
                "?",
                "!",
                ";",
            )
        )
    ):

        return 0.60

    return 0.0


# ==============================================================
# COUNT NAVIGATION REFERENCES IN ONE BLOCK
# ==============================================================


def _count_navigation_references(
    text: str,
) -> int:

    if not text.strip():
        return 0

    # Dot leaders may exist several times inside one PDF block.
    dot_count = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN
        .findall(
            text
        )
    )

    if dot_count:
        return dot_count

    lines = [
        line.strip()

        for line
        in text.splitlines()

        if line.strip()
    ]

    count = 0

    for line in lines:

        normalized_line = (
            _normalize_content_text(
                line
            )
        )

        if (
            NUMBERED_PAGE_REFERENCE_PATTERN
            .fullmatch(
                normalized_line
            )

            or
            FIGURE_TABLE_PAGE_REFERENCE_PATTERN
            .fullmatch(
                normalized_line
            )
        ):

            count += 1

    return count


# ==============================================================
# STRUCTURAL TITLE NORMALIZATION
# ==============================================================


def _normalize_section_title(
    value: str,
) -> str:

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

    value = value.rstrip(
        " :.-–—"
    )

    return value


# ==============================================================
# CONTENT NORMALIZATION
# ==============================================================


def _normalize_content_text(
    value: str,
) -> str:

    value = (
        str(
            value
        )
        .replace(
            "\r",
            " ",
        )
        .replace(
            "\n",
            " ",
        )
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


# ==============================================================
# SPLIT ONE OVERSIZED SOURCE ELEMENT
# ==============================================================


def _split_long_text(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:

    text = (
        text.strip()
    )

    if not text:
        return []

    if max_chars <= 0:

        raise ValueError(
            "max_chars must be greater than 0"
        )

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

    pieces: list[str] = []

    start = 0

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

        if hard_end >= len(text):

            end = len(text)

        else:

            search_start = min(
                start
                + minimum_break_distance,

                hard_end,
            )

            break_positions: list[
                int
            ] = []

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

                if position != -1:

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

            if len(piece) <= max_chars:

                pieces.append(
                    piece
                )

            else:

                pieces.append(
                    piece[
                        :max_chars
                    ]
                    .strip()
                )

        if end >= len(text):
            break

        # ======================================================
        # CHARACTER OVERLAP
        # ======================================================

        next_start = max(
            start + 1,
            end - overlap_chars,
        )

        # Avoid beginning in the middle of a word.
        while (
            next_start < end

            and
            not text[
                next_start
            ].isspace()
        ):

            next_start += 1

        while (
            next_start < len(text)

            and
            text[
                next_start
            ].isspace()
        ):

            next_start += 1

        if next_start <= start:

            next_start = end

        start = next_start

    return pieces