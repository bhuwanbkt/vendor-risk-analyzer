from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any

from vendor_risk_analyzer.ingestion.parsers.base import ParsedElement


# ==============================================================
# STRONG NAVIGATION HEADING HINTS
#
# These headings strongly suggest that neighboring content may
# be navigation.
#
# IMPORTANT:
#
# They DO NOT automatically cause every child element to be
# excluded.
#
# Each child element is evaluated independently.
#
# We deliberately do NOT include:
#
#   Preface
#   Foreword
#   Executive Summary
#   Appendix
#   Glossary
#   References
#   Bibliography
#   Notices
#   Compliance
#   Security Addendum
#   Privacy Addendum
#
# because those sections may contain important vendor-risk
# evidence.
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


# Weak signal.
#
# Examples:
#
# Security Architecture 8
# Preface iv
#
# This is NEVER trusted by itself.
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
    source_element_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ==============================================================
# MAIN CHUNKING FUNCTION
# ==============================================================


def create_chunks(
    elements: list[ParsedElement],
    max_chars: int = 1200,
    overlap_elements: int = 1,
    long_text_overlap_chars: int = 160,
) -> list[ParsedChunk]:
    """
    Convert parsed document elements into retrieval chunks.

    Important behavior:

    - headings define retrieval context
    - captions are preserved as source elements but are not
      standalone retrieval chunks
    - tables stay standalone
    - navigation entries are preserved in document_elements but
      excluded from retrieval chunks
    - substantive content accidentally inheriting a navigation
      heading is preserved
    - navigation-only headings are removed from retrieval
      heading_path metadata
    - oversized single text elements are safely split
    - normal chunks support element-level overlap
    """

    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0")

    if overlap_elements < 0:
        raise ValueError("overlap_elements cannot be negative")

    if long_text_overlap_chars < 0:
        raise ValueError(
            "long_text_overlap_chars cannot be negative"
        )

    # ----------------------------------------------------------
    # Analyze the complete document first.
    #
    # Result:
    #
    # {
    #     element_sequence: exclusion_reason
    # }
    #
    # Only individual navigation-like child elements are
    # excluded.
    # ----------------------------------------------------------

    navigation_exclusions = _detect_navigation_elements(
        elements
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

        content = "\n\n".join(current_parts).strip()

        if not content:
            return

        page_numbers = sorted(
            {
                page
                for page in current_pages
                if page is not None
            }
        )

        chunks.append(
            ParsedChunk(
                content=content,
                source_sequences=current_sequences.copy(),
                metadata={
                    "heading_path":
                        _sanitize_retrieval_heading_path(
                            current_heading_path
                        ),
                    "element_type": "text",
                    "overlap_elements": overlap_elements,
                    "page_numbers": page_numbers,
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

    for sequence, element in enumerate(elements):

        # ======================================================
        # HEADING
        #
        # Headings define structural context.
        #
        # They do not become standalone retrieval chunks.
        #
        # Navigation-only headings are removed from retrieval
        # context, but remain untouched in document_elements.
        # ======================================================

        if element.element_type == "heading":
            save_text_chunk()
            clear_current()

            current_heading_path[:] = (
                _sanitize_retrieval_heading_path(
                    element.heading_path
                )
            )

            continue

        # ======================================================
        # NAVIGATION CHILD ELEMENT
        #
        # Example:
        #
        #   Data Encryption ............ 5
        #
        # The original ParsedElement is preserved.
        # Only retrieval chunk creation is skipped.
        # ======================================================

        if sequence in navigation_exclusions:
            save_text_chunk()
            clear_current()

            if element.heading_path:
                current_heading_path[:] = (
                    _sanitize_retrieval_heading_path(
                        element.heading_path
                    )
                )

            continue

        # ======================================================
        # CAPTION
        #
        # Figure captions remain in document_elements.
        #
        # Table captions are already attached to table metadata
        # by the parser and are prepended to table retrieval
        # chunks below.
        # ======================================================

        if element.element_type == "caption":
            continue

        # ======================================================
        # TABLE
        #
        # Tables remain semantic units.
        #
        # We intentionally do not force arbitrary 1200-character
        # splitting on tables because that could destroy
        # row/column meaning.
        # ======================================================

        if element.element_type == "table":
            save_text_chunk()
            clear_current()

            raw_table_heading_path = (
                element.heading_path.copy()
                if element.heading_path
                else current_heading_path.copy()
            )

            table_heading_path = (
                _sanitize_retrieval_heading_path(
                    raw_table_heading_path
                )
            )

            caption = element.metadata.get("caption")

            table_content = element.content.strip()

            if caption:
                normalized_caption = str(
                    caption
                ).strip()

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
                    content=table_content,
                    source_sequences=[sequence],
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

        text = element.content.strip()

        if not text:
            continue

        # ------------------------------------------------------
        # This is the important final fix.
        #
        # Suppose the source PDF says:
        #
        # Table of Contents
        #   ...
        #   This whitepaper is for historical reference only.
        #
        # The source element may correctly/technically inherit:
        #
        # ["Table of Contents"]
        #
        # But because this child itself is NOT navigation, it is
        # retained and its retrieval heading becomes:
        #
        # []
        #
        # The original element remains unchanged in the DB.
        # ------------------------------------------------------

        if element.heading_path:
            current_heading_path[:] = (
                _sanitize_retrieval_heading_path(
                    element.heading_path
                )
            )

        # ======================================================
        # SINGLE SOURCE ELEMENT > max_chars
        # ======================================================

        if len(text) > max_chars:
            save_text_chunk()
            clear_current()

            parts = _split_long_text(
                text,
                max_chars=max_chars,
                overlap_chars=long_text_overlap_chars,
            )

            part_count = len(parts)

            for part_index, part in enumerate(
                parts,
                start=1,
            ):
                if not part:
                    continue

                chunks.append(
                    ParsedChunk(
                        content=part,
                        source_sequences=[sequence],
                        metadata={
                            "heading_path":
                                _sanitize_retrieval_heading_path(
                                    current_heading_path
                                ),
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
                                    if element.page_number
                                    is not None
                                    else []
                                ),
                        },
                    )
                )

            continue

        # ======================================================
        # NORMAL MULTI-ELEMENT CHUNKING
        # ======================================================

        candidate = "\n\n".join(
            current_parts + [text]
        )

        # ------------------------------------------------------
        # Adding this element would exceed max_chars.
        # ------------------------------------------------------

        if (
            current_parts
            and len(candidate) > max_chars
        ):
            save_text_chunk()

            # --------------------------------------------------
            # Preserve element-level overlap.
            #
            # Example with overlap_elements=1:
            #
            # Chunk A:
            #   P1
            #   P2
            #   P3
            #
            # Chunk B:
            #   P3
            #   P4
            # --------------------------------------------------

            overlap_parts = (
                current_parts[-overlap_elements:]
                if overlap_elements > 0
                else []
            )

            overlap_sequences = (
                current_sequences[-overlap_elements:]
                if overlap_elements > 0
                else []
            )

            overlap_pages = (
                current_pages[-overlap_elements:]
                if overlap_elements > 0
                else []
            )

            overlap_candidate = "\n\n".join(
                overlap_parts + [text]
            )

            if (
                overlap_parts
                and
                len(overlap_candidate)
                <= max_chars
            ):
                current_parts[:] = overlap_parts
                current_sequences[:] = (
                    overlap_sequences
                )
                current_pages[:] = overlap_pages

            else:
                clear_current()

        current_parts.append(text)
        current_sequences.append(sequence)
        current_pages.append(
            element.page_number
        )

    # ==========================================================
    # SAVE FINAL TEXT CHUNK
    # ==============================================================

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

    Example source:

        Table of Contents

        Security ............ 4
        Encryption .......... 7

        This document is historical.

    Result:

        Security ............ 4
            -> excluded

        Encryption .......... 7
            -> excluded

        This document is historical.
            -> preserved

    A navigation-looking heading is only a contextual signal.
    It does not automatically remove every child.
    """

    groups: dict[
        tuple[str, ...],
        list[
            tuple[
                int,
                ParsedElement,
            ]
        ],
    ] = defaultdict(list)

    # ==========================================================
    # GROUP ELEMENTS BY STRUCTURAL CONTEXT
    # ==============================================================

    for sequence, element in enumerate(
        elements
    ):
        key = _navigation_context_key(
            element
        )

        groups[key].append(
            (
                sequence,
                element,
            )
        )

    exclusions: dict[int, str] = {}

    # ==========================================================
    # INSPECT EACH STRUCTURAL GROUP
    # ==============================================================

    for _, members in groups.items():

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
            for sequence, element in members
            if (
                element.element_type
                != "heading"
                and element.content.strip()
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

        # ------------------------------------------------------
        # Group density is supporting evidence only.
        #
        # It never means:
        #
        #     exclude every child.
        # ------------------------------------------------------

        medium_count = sum(
            1
            for score in scores.values()
            if score >= 0.60
        )

        content_count = len(
            content_members
        )

        medium_density = (
            medium_count
            / content_count
        )

        # ======================================================
        # CLASSIFY EACH CHILD INDEPENDENTLY
        # ==============================================================

        for sequence, element in content_members:

            score = scores[
                sequence
            ]

            text = element.content

            # --------------------------------------------------
            # CASE 1
            #
            # Very strong navigation evidence.
            #
            # Examples:
            #
            # Security ............. 4
            # Compliance .......... 12
            # --------------------------------------------------

            if score >= 0.90:
                exclusions[
                    sequence
                ] = (
                    "strong_navigation_pattern"
                )

                continue

            # --------------------------------------------------
            # CASE 2
            #
            # Strong numbered / figure / table reference.
            #
            # Example:
            #
            # 2.1 Encryption 7
            # --------------------------------------------------

            if score >= 0.85:
                exclusions[
                    sequence
                ] = (
                    "navigation_reference"
                )

                continue

            # --------------------------------------------------
            # CASE 3
            #
            # Weak page-reference signal plus explicitly
            # navigation-looking parent heading.
            #
            # Example:
            #
            # Table of Contents
            #
            # Security Architecture 8
            # --------------------------------------------------

            if (
                strong_navigation_context
                and score >= 0.60
            ):
                exclusions[
                    sequence
                ] = (
                    "navigation_heading_and_pattern"
                )

                continue

            # --------------------------------------------------
            # CASE 4
            #
            # Heading itself is not obvious navigation, but most
            # sibling elements look like navigation.
            #
            # We still remove ONLY matching children.
            # --------------------------------------------------

            if (
                content_count >= 3
                and medium_density >= 0.70
                and score >= 0.60
            ):
                exclusions[
                    sequence
                ] = (
                    "navigation_pattern_density"
                )

                continue

            # --------------------------------------------------
            # CASE 5
            #
            # One large parser block may contain many TOC
            # references.
            #
            # Example:
            #
            # Security ........ 5
            # Privacy ......... 8
            # Compliance ..... 11
            # --------------------------------------------------

            if (
                _count_navigation_references(
                    text
                )
                >= 3
                and score >= 0.85
            ):
                exclusions[
                    sequence
                ] = (
                    "navigation_reference_block"
                )

                continue

            # Otherwise:
            #
            # preserve the element for retrieval.

    return exclusions


# ==============================================================
# DOES GROUP HAVE A STRONG NAVIGATION HEADING?
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

        for heading in element.heading_path:

            if _is_strong_navigation_title(
                heading
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
    """
    Group elements by the strongest structural information
    available.

    Priority:

    1. heading_path
    2. section_title
    3. page_number
    4. unscoped fallback

    Page fallback prevents one unheaded navigation page from
    causing unrelated unheaded pages to be grouped with it.
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

    if element.page_number is not None:

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

    # ----------------------------------------------------------
    # Also support:
    #
    # Table of Contents - Security Policy
    # Contents: Vendor Handbook
    # List of Tables — Appendix
    #
    # without broadly matching arbitrary prose.
    # ----------------------------------------------------------

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
# RETRIEVAL HEADING SANITIZATION
# ==============================================================


def _sanitize_retrieval_heading_path(
    heading_path: list[str],
) -> list[str]:
    """
    Remove navigation-only headings from retrieval metadata.

    The original ParsedElement is NEVER modified.

    Examples:

        ["Table of Contents"]
            ->
        []

        ["Security Manual", "Table of Contents"]
            ->
        ["Security Manual"]

        ["Vendor Assessment", "List of Figures"]
            ->
        ["Vendor Assessment"]

        ["Appendix A. Security Controls"]
            ->
        ["Appendix A. Security Controls"]

        ["Appendix C. Glossary"]
            ->
        ["Appendix C. Glossary"]

    This creates an intentional distinction:

        document_elements
            = faithful source representation

        document_chunks
            = retrieval-optimized representation
    """

    return [
        heading
        for heading in heading_path
        if not _is_strong_navigation_title(
            heading
        )
    ]


# ==============================================================
# NAVIGATION EVIDENCE SCORE
# ==============================================================


def _navigation_evidence_score(
    text: str,
) -> float:
    """
    Return a navigation-confidence score from 0.0 to 1.0.

    The detector intentionally favors preserving content when
    uncertain.

    It is safer to embed one unnecessary navigation line than
    to throw away meaningful vendor-risk evidence.
    """

    normalized = (
        _normalize_content_text(
            text
        )
    )

    if not normalized:
        return 0.0

    # ==========================================================
    # DOT-LEADER PAGE REFERENCES
    #
    # Very strong navigation signal.
    # ==============================================================

    dot_reference_count = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN.findall(
            normalized
        )
    )

    if dot_reference_count >= 2:
        return 1.0

    if dot_reference_count == 1:
        return 0.95

    # ==========================================================
    # FIGURE / TABLE INDEX ENTRY
    # ==============================================================

    if (
        FIGURE_TABLE_PAGE_REFERENCE_PATTERN.fullmatch(
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
    # ==============================================================

    if (
        len(normalized) <= 180
        and
        NUMBERED_PAGE_REFERENCE_PATTERN.fullmatch(
            normalized
        )
    ):
        return 0.85

    # ==========================================================
    # SHORT TITLE + PAGE NUMBER
    #
    # Weak evidence only.
    #
    # Example:
    #
    # Security Architecture 8
    # ==============================================================

    word_count = len(
        normalized.split()
    )

    if (
        len(normalized) <= 120
        and word_count <= 14
        and
        SHORT_TITLE_PAGE_REFERENCE_PATTERN.fullmatch(
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
# COUNT NAVIGATION REFERENCES INSIDE ONE BLOCK
# ==============================================================


def _count_navigation_references(
    text: str,
) -> int:
    """
    Count navigation-style references inside one parser block.

    This is useful when a PDF parser returns an entire TOC as a
    single paragraph element.
    """

    if not text.strip():
        return 0

    dot_count = len(
        DOT_LEADER_PAGE_REFERENCE_PATTERN.findall(
            text
        )
    )

    if dot_count:
        return dot_count

    lines = [
        line.strip()
        for line in text.splitlines()
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
            NUMBERED_PAGE_REFERENCE_PATTERN.fullmatch(
                normalized_line
            )
            or
            FIGURE_TABLE_PAGE_REFERENCE_PATTERN.fullmatch(
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
        str(value)
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
        str(value)
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
    """
    Split one oversized source element while preserving natural
    boundaries when possible.

    Preferred boundaries:

    1. paragraph
    2. newline
    3. sentence
    4. semicolon
    5. comma
    6. whitespace
    7. hard character boundary

    Adjacent pieces preserve character-level overlap.
    """

    text = text.strip()

    if not text:
        return []

    if max_chars <= 0:
        raise ValueError(
            "max_chars must be greater than 0"
        )

    # Keep overlap from becoming too large relative to the
    # actual chunk size.

    overlap_chars = max(
        0,
        min(
            overlap_chars,
            max_chars // 3,
        ),
    )

    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []

    start = 0

    # Prefer boundaries in the last 40% of the available window
    # so chunks do not become unnecessarily tiny.

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

        # ======================================================
        # LAST PIECE
        # ==============================================================

        if hard_end >= len(text):
            end = len(text)

        else:

            search_start = min(
                start
                + minimum_break_distance,
                hard_end,
            )

            break_positions: list[int] = []

            # --------------------------------------------------
            # Find the latest natural boundary.
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

                position = text.rfind(
                    separator,
                    search_start,
                    hard_end,
                )

                if position != -1:
                    break_positions.append(
                        position
                        + len(separator)
                    )

            end = (
                max(break_positions)
                if break_positions
                else hard_end
            )

        piece = text[
            start:end
        ].strip()

        if piece:

            # Defensive size guarantee.
            if len(piece) <= max_chars:
                pieces.append(
                    piece
                )

            else:
                pieces.append(
                    piece[
                        :max_chars
                    ].strip()
                )

        if end >= len(text):
            break

        # ======================================================
        # CHARACTER OVERLAP
        # ==============================================================

        next_start = max(
            start + 1,
            end - overlap_chars,
        )

        # ------------------------------------------------------
        # Avoid starting the next piece inside a word.
        # ------------------------------------------------------

        while (
            next_start < end
            and
            not text[
                next_start
            ].isspace()
        ):
            next_start += 1

        # ------------------------------------------------------
        # Remove leading whitespace.
        # ------------------------------------------------------

        while (
            next_start < len(text)
            and
            text[
                next_start
            ].isspace()
        ):
            next_start += 1

        # ------------------------------------------------------
        # Infinite-loop protection.
        # ------------------------------------------------------

        if next_start <= start:
            next_start = end

        start = next_start

    return pieces