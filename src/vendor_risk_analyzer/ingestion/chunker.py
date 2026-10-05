from collections import defaultdict
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
# NAVIGATION DETECTION
#
# IMPORTANT:
#
# We intentionally keep this list small.
#
# We DO NOT put things such as:
#
#   Preface
#   Foreword
#   Appendix
#   Glossary
#   References
#   Executive Summary
#   Notices
#   Compliance
#   Security Addendum
#   Privacy Addendum
#
# here because those sections can contain important vendor-risk
# evidence.
#
# These are only very strong navigation hints.
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
# Security Controls ........................ 12
# Appendix A ............................... 24
# Preface .................................. iv
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
# This is intentionally conservative and is only trusted when
# several similar entries occur in the same section/page.
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


# Generic short title followed by a page number.
#
# Example:
#
# Security Architecture 8
#
# This is weak evidence and is NEVER enough by itself.
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
    # FIRST:
    #
    # Analyze the full document before chunking it.
    #
    # We need document-level / section-level context to decide
    # whether content is navigation.
    #
    # A single line such as:
    #
    #   Security Architecture 8
    #
    # is NOT enough to discard content.
    #
    # But 10 similar page-reference lines in the same section
    # strongly indicate navigation.
    # ==========================================================

    navigation_exclusions = (
        _detect_navigation_elements(
            elements
        )
    )

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
    # SAVE CURRENT TEXT CHUNK
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
    # CLEAR CURRENT CHUNK
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
        # Headings do not create standalone retrieval chunks.
        # They provide structural context.
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
        # NAVIGATION CONTENT
        #
        # Keep it in document_elements.
        #
        # Do NOT put it in document_chunks.
        # ======================================================

        if (
            sequence
            in navigation_exclusions
        ):

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
        # Figure captions remain available in document_elements.
        #
        # Table captions are already attached to table metadata
        # by the PDF parser.
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
        # Do not arbitrarily split them at max_chars because that
        # can destroy row/column relationships.
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

        if (
            len(
                text
            )
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
            len(
                candidate
            )
            > max_chars
        ):

            save_text_chunk()

            # --------------------------------------------------
            # Preserve element-level overlap.
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

    # ==========================================================
    # SAVE FINAL CHUNK
    # ==========================================================

    save_text_chunk()

    return chunks


# ==============================================================
# NAVIGATION ANALYSIS
# ==============================================================


def _detect_navigation_elements(
    elements: list[ParsedElement],
) -> dict[int, str]:
    """
    Analyze the entire document and return:

        {
            element_sequence:
                reason_for_retrieval_exclusion
        }

    This is deliberately section-aware.

    We do NOT decide that content is navigation only because its
    heading contains words such as "Appendix" or "Preface".
    """

    grouped_elements: dict[
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
    # GROUP ELEMENTS BY STRUCTURAL CONTEXT
    # ==========================================================

    for (
        sequence,
        element,
    ) in enumerate(
        elements
    ):

        context_key = (
            _navigation_context_key(
                element
            )
        )

        grouped_elements[
            context_key
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
    # CLASSIFY EACH STRUCTURAL GROUP
    # ==========================================================

    for (
        context_key,
        members,
    ) in grouped_elements.items():

        reason = (
            _classify_navigation_group(
                context_key=
                    context_key,

                members=
                    members,
            )
        )

        if reason is None:
            continue

        for (
            sequence,
            element,
        ) in members:

            # Headings never become retrieval chunks anyway.
            if (
                element.element_type
                == "heading"
            ):
                continue

            exclusions[
                sequence
            ] = reason

    return exclusions


# ==============================================================
# NAVIGATION CONTEXT KEY
# ==============================================================


def _navigation_context_key(
    element: ParsedElement,
) -> tuple[str, ...]:
    """
    Create a grouping key.

    Best case:
        heading hierarchy.

    Fallback:
        section title.

    If there is no structural information, use page scope so
    that one navigation-heavy page does not cause unrelated
    unheaded pages to be excluded.
    """

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

    # TXT and other documents may have neither page nor
    # structural hierarchy.
    return (
        "unscoped",
    )


# ==============================================================
# CLASSIFY ONE STRUCTURAL GROUP
# ==============================================================


def _classify_navigation_group(
    *,
    context_key: tuple[str, ...],
    members: list[
        tuple[
            int,
            ParsedElement,
        ]
    ],
) -> str | None:

    # ==========================================================
    # SIGNAL 1:
    # Strong structural heading
    # ==========================================================

    structural_titles: list[
        str
    ] = []

    for (
        _,
        element,
    ) in members:

        for heading in (
            element.heading_path
        ):

            normalized = (
                _normalize_section_title(
                    heading
                )
            )

            if normalized:

                structural_titles.append(
                    normalized
                )

        if element.section_title:

            normalized = (
                _normalize_section_title(
                    element.section_title
                )
            )

            if normalized:

                structural_titles.append(
                    normalized
                )

    if any(
        _is_strong_navigation_title(
            title
        )

        for title
        in structural_titles
    ):

        return (
            "strong_navigation_heading"
        )

    # ==========================================================
    # SIGNAL 2:
    # Content-pattern density
    # ==========================================================

    content_members: list[
        ParsedElement
    ] = [
        element

        for (
            _,
            element,
        ) in members

        if (
            element.element_type
            != "heading"

            and
            element.content.strip()
        )
    ]

    if not content_members:
        return None

    evidence_scores = [
        _navigation_evidence_score(
            element.content
        )

        for element
        in content_members
    ]

    strong_evidence_count = sum(
        1

        for score
        in evidence_scores

        if score >= 0.85
    )

    medium_evidence_count = sum(
        1

        for score
        in evidence_scores

        if score >= 0.60
    )

    average_score = (
        sum(
            evidence_scores
        )
        /
        len(
            evidence_scores
        )
    )

    # ==========================================================
    # CASE A:
    #
    # One PDF block may contain an entire TOC.
    #
    # Example:
    #
    # Security ............ 4
    # Compliance .......... 8
    # Appendix ............ 13
    #
    # PyMuPDF may expose that as one element.
    # ==========================================================

    if (
        len(
            content_members
        )
        == 1
    ):

        only_text = (
            content_members[
                0
            ]
            .content
        )

        repeated_references = (
            _count_navigation_references(
                only_text
            )
        )

        if (
            evidence_scores[
                0
            ]
            >= 0.90

            and
            repeated_references
            >= 3
        ):

            return (
                "navigation_reference_block"
            )

        return None

    # ==========================================================
    # CASE B:
    #
    # Multiple strongly navigation-looking elements.
    #
    # Require density rather than one accidental match.
    # ==========================================================

    if (
        len(
            content_members
        )
        >= 3

        and
        strong_evidence_count
        >= 2

        and
        average_score
        >= 0.55
    ):

        return (
            "navigation_pattern_density"
        )

    # ==========================================================
    # CASE C:
    #
    # Larger groups can contain a few odd elements.
    #
    # If 70% or more look navigation-like, classify the group.
    # ==========================================================

    if (
        len(
            content_members
        )
        >= 5
    ):

        density = (
            medium_evidence_count
            /
            len(
                content_members
            )
        )

        if density >= 0.70:

            return (
                "navigation_pattern_density"
            )

    return None


# ==============================================================
# STRONG NAVIGATION TITLE
# ==============================================================


def _is_strong_navigation_title(
    title: str,
) -> bool:

    title = (
        _normalize_section_title(
            title
        )
    )

    if (
        title
        in
        STRONG_NAVIGATION_TITLES
    ):

        return True

    # ----------------------------------------------------------
    # Also support:
    #
    # "Contents — Security Manual"
    # "Table of Contents - Policy"
    #
    # without treating something such as:
    #
    # "Contents of Encryption Keys"
    #
    # as navigation.
    # ----------------------------------------------------------

    for navigation_title in (
        STRONG_NAVIGATION_TITLES
    ):

        if (
            title.startswith(
                navigation_title
                + " - "
            )

            or

            title.startswith(
                navigation_title
                + " — "
            )

            or

            title.startswith(
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
    """
    Return a score from 0.0 to 1.0.

    1.0:
        very strong navigation evidence.

    0.0:
        no navigation evidence.

    This function intentionally favors false negatives over
    false positives.

    It is better to embed one unnecessary TOC line than to
    remove important security evidence.
    """

    normalized = (
        _normalize_content_text(
            text
        )
    )

    if not normalized:
        return 0.0

    # ==========================================================
    # DOT-LEADER REFERENCES
    #
    # Very strong evidence.
    # ==========================================================

    dot_reference_count = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN
        .findall(
            normalized
        )
    )

    if (
        dot_reference_count
        >= 2
    ):

        return 1.0

    if (
        dot_reference_count
        == 1
    ):

        return 0.95

    # ==========================================================
    # FIGURE/TABLE INDEX ENTRY
    # ==========================================================

    if (
        FIGURE_TABLE_PAGE_REFERENCE_PATTERN
        .fullmatch(
            normalized
        )
    ):

        return 0.95

    # ==========================================================
    # NUMBERED TOC ENTRY
    #
    # Example:
    #
    # 2.1 Security Controls 8
    # ==========================================================

    if (
        len(
            normalized
        )
        <= 180

        and
        NUMBERED_PAGE_REFERENCE_PATTERN
        .fullmatch(
            normalized
        )
    ):

        return 0.85

    # ==========================================================
    # GENERIC SHORT TITLE + PAGE NUMBER
    #
    # Weak evidence.
    #
    # This will only trigger navigation classification when many
    # sibling elements look similar.
    # ==========================================================

    word_count = len(
        normalized.split()
    )

    if (
        len(
            normalized
        )
        <= 120

        and
        word_count
        <= 14

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
# COUNT NAVIGATION REFERENCES
# ==============================================================


def _count_navigation_references(
    text: str,
) -> int:
    """
    Count strong page-reference patterns inside a block.

    Useful when an entire TOC has been extracted as one PDF
    element.
    """

    normalized = (
        _normalize_content_text(
            text
        )
    )

    if not normalized:
        return 0

    dot_references = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN
        .findall(
            normalized
        )
    )

    if dot_references:
        return dot_references

    # ----------------------------------------------------------
    # If line structure survived parsing, evaluate individual
    # lines too.
    # ----------------------------------------------------------

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
# SECTION TITLE NORMALIZATION
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
# SPLIT ONE OVERSIZED TEXT ELEMENT
# ==============================================================


def _split_long_text(
    text: str,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[str]:
    """
    Split one oversized source element while attempting to
    preserve natural boundaries.

    Preferred boundaries:

        paragraph
        newline
        sentence
        semicolon
        comma
        whitespace
        hard character boundary

    Adjacent pieces preserve a small character overlap.
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

        # ------------------------------------------------------
        # LAST PIECE
        # ------------------------------------------------------

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
            # Search for natural boundaries.
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

            # Defensive guarantee.
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

        # ======================================================
        # CHARACTER OVERLAP
        # ======================================================

        next_start = max(
            start + 1,

            end
            - overlap_chars,
        )

        # Avoid beginning in the middle of a word.
        while (
            next_start
            < end

            and
            not text[
                next_start
            ].isspace()
        ):

            next_start += 1

        # Remove whitespace at the start of the next chunk.
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