from __future__ import annotations

import re

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


# ==============================================================
# MARKDOWN STRUCTURE PATTERNS
# ==============================================================


ATX_HEADING_PATTERN = re.compile(
    r"^ {0,3}"
    r"(?P<hashes>#{1,6})"
    r"[ \t]+"
    r"(?P<title>.*?)"
    r"[ \t]*"
    r"#*"
    r"[ \t]*$"
)


SETEXT_HEADING_PATTERN = re.compile(
    r"^ {0,3}"
    r"(?P<marker>=+|-+)"
    r"[ \t]*$"
)


LIST_ITEM_PATTERN = re.compile(
    r"^"
    r"(?P<indent>[ \t]*)"
    r"(?P<marker>"
    r"[-+*]"
    r"|"
    r"\d+[.)]"
    r")"
    r"[ \t]+"
    r"(?P<content>.+?)"
    r"[ \t]*$"
)


FENCE_PATTERN = re.compile(
    r"^ {0,3}"
    r"(?P<fence>`{3,}|~{3,})"
    r"(?P<info>.*)$"
)


TABLE_CAPTION_PATTERN = re.compile(
    r"^table\s+"
    r"[A-Za-z]?\d+"
    r"\s*"
    r"[.:\-–—]"
    r"\s+"
    r"\S",
    re.IGNORECASE,
)


FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:fig(?:ure)?\.?)\s+"
    r"[A-Za-z]?\d+"
    r"\s*"
    r"[.:\-–—]"
    r"\s+"
    r"\S",
    re.IGNORECASE,
)


TABLE_SEPARATOR_CELL_PATTERN = re.compile(
    r"^"
    r":?"
    r"-{3,}"
    r":?"
    r"$"
)


# ==============================================================
# MARKDOWN PARSER
# ==============================================================


class MarkdownParser(BaseParser):
    """
    Structure-aware Markdown parser.

    Preserves:

    - ATX headings:
        # Heading
        ## Heading
        ### Heading

    - Setext headings:
        Heading
        =======

    - paragraphs

    - bullet lists

    - numbered lists

    - nested list levels

    - GitHub-style Markdown tables

    - table / figure captions

    - fenced code blocks

    - original document order

    Navigation filtering itself remains the responsibility of
    the shared chunker.

    Page numbers are intentionally None because Markdown has no
    stable concept of pagination.
    """

    supported_extensions = {
        "md",
        "markdown",
    }

    parser_version = "2.0"

    # ==========================================================
    # PUBLIC PARSE
    # ==========================================================

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        text = content.decode(
            "utf-8",
            errors="replace",
        )

        # Remove UTF-8 BOM if present.
        text = text.lstrip("\ufeff")

        # Normalize line endings.
        text = (
            text.replace(
                "\r\n",
                "\n",
            )
            .replace(
                "\r",
                "\n",
            )
        )

        lines = text.split("\n")

        elements: list[ParsedElement] = []

        # ------------------------------------------------------
        # Heading hierarchy:
        #
        # {
        #     1: "Vendor Security Assessment",
        #     2: "Security Controls",
        #     3: "Encryption",
        # }
        #
        # becomes:
        #
        # [
        #     "Vendor Security Assessment",
        #     "Security Controls",
        #     "Encryption",
        # ]
        # ------------------------------------------------------

        heading_stack: dict[
            int,
            str,
        ] = {}

        # Used to infer nested Markdown list levels.
        list_indent_stack: list[int] = []

        pending_table_caption: str | None = None

        table_index = 0

        i = 0

        while i < len(lines):

            raw_line = lines[i]
            stripped = raw_line.strip()

            # ==================================================
            # BLANK LINE
            # ==================================================

            if not stripped:

                # A blank line ends the current list block.
                list_indent_stack.clear()

                # Do NOT clear pending_table_caption here.
                #
                # Markdown commonly contains:
                #
                # ### Table 1. Security Controls
                #
                # | Control | Status |
                # | --- | --- |
                #
                # so one or more blank lines between caption and
                # table are valid.

                i += 1
                continue

            # ==================================================
            # FENCED CODE BLOCK
            #
            # Handle before headings/lists/tables so Markdown-like
            # syntax inside code is never interpreted as document
            # structure.
            # ==================================================

            fence_match = FENCE_PATTERN.match(
                raw_line
            )

            if fence_match:

                list_indent_stack.clear()
                pending_table_caption = None

                fence = fence_match.group(
                    "fence"
                )

                fence_char = fence[0]
                minimum_fence_length = len(
                    fence
                )

                info = (
                    fence_match.group(
                        "info"
                    )
                    .strip()
                )

                code_lines: list[str] = []

                i += 1

                while i < len(lines):

                    candidate = lines[i]

                    if self._is_closing_fence(
                        candidate,
                        fence_char=
                            fence_char,
                        minimum_length=
                            minimum_fence_length,
                    ):
                        i += 1
                        break

                    code_lines.append(
                        candidate
                    )

                    i += 1

                code_content = "\n".join(
                    code_lines
                ).strip("\n")

                if code_content:

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "code_block",

                            content=
                                code_content,

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata={
                                "language":
                                    info or None,

                                "fence":
                                    fence_char,
                            },
                        )
                    )

                continue

            # ==================================================
            # ATX HEADING
            #
            # # Heading
            # ## Heading
            # ### Heading
            # ==================================================

            heading_match = (
                ATX_HEADING_PATTERN.match(
                    raw_line
                )
            )

            if heading_match:

                list_indent_stack.clear()

                heading_level = len(
                    heading_match.group(
                        "hashes"
                    )
                )

                title = (
                    self._normalize_inline_text(
                        heading_match.group(
                            "title"
                        )
                    )
                )

                if not title:
                    i += 1
                    continue

                # ----------------------------------------------
                # A heading-looking line may actually be a table
                # or figure caption.
                #
                # Example:
                #
                # ### Table 1. Security Controls
                #
                # It should NOT become part of heading_path.
                # ----------------------------------------------

                caption_type = (
                    self._get_caption_type(
                        title
                    )
                )

                if caption_type is not None:

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "caption",

                            content=
                                title,

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata={
                                "caption_type":
                                    caption_type,

                                "heading_level":
                                    heading_level,

                                "source_syntax":
                                    "atx_heading",
                            },
                        )
                    )

                    if caption_type == "table":

                        pending_table_caption = (
                            title
                        )

                    else:

                        pending_table_caption = (
                            None
                        )

                    i += 1
                    continue

                pending_table_caption = None

                self._update_heading_stack(
                    heading_stack,
                    level=
                        heading_level,
                    title=
                        title,
                )

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type=
                            "heading",

                        content=
                            title,

                        page_number=
                            None,

                        section_title=
                            title,

                        heading_path=
                            heading_path,

                        metadata={
                            "heading_level":
                                heading_level,

                            "source_syntax":
                                "atx",
                        },
                    )
                )

                i += 1
                continue

            # ==================================================
            # SETEXT HEADING
            #
            # Example:
            #
            # Security Policy
            # ===============
            #
            # or:
            #
            # Encryption
            # ----------
            # ==================================================

            if (
                i + 1 < len(lines)
                and
                stripped
                and
                SETEXT_HEADING_PATTERN.match(
                    lines[i + 1]
                )
            ):

                underline_match = (
                    SETEXT_HEADING_PATTERN.match(
                        lines[i + 1]
                    )
                )

                assert underline_match is not None

                list_indent_stack.clear()

                marker = underline_match.group(
                    "marker"
                )

                heading_level = (
                    1
                    if marker.startswith("=")
                    else 2
                )

                title = (
                    self._normalize_inline_text(
                        stripped
                    )
                )

                caption_type = (
                    self._get_caption_type(
                        title
                    )
                )

                if caption_type is not None:

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "caption",

                            content=
                                title,

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata={
                                "caption_type":
                                    caption_type,

                                "heading_level":
                                    heading_level,

                                "source_syntax":
                                    "setext_heading",
                            },
                        )
                    )

                    if caption_type == "table":

                        pending_table_caption = (
                            title
                        )

                    else:

                        pending_table_caption = (
                            None
                        )

                    i += 2
                    continue

                pending_table_caption = None

                self._update_heading_stack(
                    heading_stack,
                    level=
                        heading_level,
                    title=
                        title,
                )

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type=
                            "heading",

                        content=
                            title,

                        page_number=
                            None,

                        section_title=
                            title,

                        heading_path=
                            heading_path,

                        metadata={
                            "heading_level":
                                heading_level,

                            "source_syntax":
                                "setext",
                        },
                    )
                )

                i += 2
                continue

            # ==================================================
            # MARKDOWN TABLE
            #
            # Example:
            #
            # | Control | Status |
            # | --- | --- |
            # | MFA | Implemented |
            # ==================================================

            if self._is_table_start(
                lines,
                i,
            ):

                list_indent_stack.clear()

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                header_cells = (
                    self._split_table_row(
                        lines[i]
                    )
                )

                separator_cells = (
                    self._split_table_row(
                        lines[i + 1]
                    )
                )

                alignments = (
                    self._parse_table_alignments(
                        separator_cells
                    )
                )

                rows: list[
                    list[str]
                ] = [
                    header_cells
                ]

                i += 2

                # ----------------------------------------------
                # Consume table data rows.
                # ----------------------------------------------

                while i < len(lines):

                    candidate = lines[i]

                    if not candidate.strip():
                        break

                    if "|" not in candidate:
                        break

                    cells = (
                        self._split_table_row(
                            candidate
                        )
                    )

                    if not cells:
                        break

                    rows.append(
                        cells
                    )

                    i += 1

                rows = (
                    self._normalize_table_rows(
                        rows
                    )
                )

                rows = (
                    self._remove_empty_columns(
                        rows
                    )
                )

                if rows:

                    column_count = max(
                        len(row)
                        for row
                        in rows
                    )

                    normalized_rows = [
                        row
                        + (
                            [""] *
                            (
                                column_count
                                - len(row)
                            )
                        )
                        for row
                        in rows
                    ]

                    table_content = (
                        self._table_to_markdown(
                            normalized_rows
                        )
                    )

                    metadata = {
                        "table_index":
                            table_index,

                        "row_count":
                            len(
                                normalized_rows
                            ),

                        "column_count":
                            column_count,

                        "is_continuation":
                            False,

                        "column_alignments":
                            alignments[
                                :column_count
                            ],
                    }

                    if pending_table_caption:

                        metadata[
                            "caption"
                        ] = (
                            pending_table_caption
                        )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "table",

                            content=
                                table_content,

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata=
                                metadata,
                        )
                    )

                    table_index += 1

                pending_table_caption = None

                continue

            # ==================================================
            # LIST ITEM
            #
            # Examples:
            #
            # - AES-256
            # * TLS 1.3
            # + KMS
            #
            # 1. Provision access
            # 2. Require MFA
            #
            # Nested indentation is preserved in metadata.
            # ==================================================

            list_match = LIST_ITEM_PATTERN.match(
                raw_line
            )

            if list_match:

                pending_table_caption = None

                indent_text = (
                    list_match.group(
                        "indent"
                    )
                )

                indent_width = (
                    self._indent_width(
                        indent_text
                    )
                )

                list_level = (
                    self._get_list_level(
                        indent_width,
                        list_indent_stack,
                    )
                )

                marker = (
                    list_match.group(
                        "marker"
                    )
                )

                list_kind = (
                    "bullet"
                    if marker[0] in "-+*"
                    else "numbered"
                )

                item_lines = [
                    list_match.group(
                        "content"
                    ).strip()
                ]

                current_indent = indent_width

                # ----------------------------------------------
                # Preserve continuation lines that belong to the
                # same list item.
                #
                # Example:
                #
                # - Customer data must be encrypted
                #   using AES-256 at rest.
                # ----------------------------------------------

                j = i + 1

                while j < len(lines):

                    continuation = lines[j]

                    if not continuation.strip():
                        break

                    if (
                        LIST_ITEM_PATTERN.match(
                            continuation
                        )
                    ):
                        break

                    if (
                        ATX_HEADING_PATTERN.match(
                            continuation
                        )
                    ):
                        break

                    if (
                        FENCE_PATTERN.match(
                            continuation
                        )
                    ):
                        break

                    if self._is_table_start(
                        lines,
                        j,
                    ):
                        break

                    continuation_indent = (
                        self._leading_indent_width(
                            continuation
                        )
                    )

                    if (
                        continuation_indent
                        <= current_indent
                    ):
                        break

                    item_lines.append(
                        continuation.strip()
                    )

                    j += 1

                item_content = (
                    self._normalize_inline_text(
                        " ".join(
                            item_lines
                        )
                    )
                )

                if item_content:

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "list_item",

                            content=
                                item_content,

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata={
                                "list_kind":
                                    list_kind,

                                "list_level":
                                    list_level,

                                "marker":
                                    marker,

                                "indent":
                                    indent_width,
                            },
                        )
                    )

                i = j
                continue

            # ==================================================
            # STANDALONE CAPTION
            #
            # Supports:
            #
            # Table 2. Encryption Controls
            #
            # | Control | Status |
            # | --- | --- |
            #
            # We only classify a plain-text TABLE caption if the
            # next nonblank block is actually a table.
            #
            # This prevents:
            #
            # "Table 2 contains the results."
            #
            # from becoming a caption.
            # ==================================================

            caption_type = (
                self._get_caption_type(
                    stripped
                )
            )

            if caption_type is not None:

                should_use_caption = False

                if caption_type == "table":

                    next_index = (
                        self._next_nonblank_index(
                            lines,
                            i + 1,
                        )
                    )

                    should_use_caption = (
                        next_index is not None
                        and
                        self._is_table_start(
                            lines,
                            next_index,
                        )
                    )

                elif caption_type == "figure":

                    # Markdown images are not yet converted into
                    # separate image elements, but preserving a
                    # clearly formatted Figure caption is useful.
                    should_use_caption = True

                if should_use_caption:

                    list_indent_stack.clear()

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "caption",

                            content=
                                self._normalize_inline_text(
                                    stripped
                                ),

                            page_number=
                                None,

                            section_title=
                                self._section_title(
                                    heading_path
                                ),

                            heading_path=
                                heading_path,

                            metadata={
                                "caption_type":
                                    caption_type,

                                "source_syntax":
                                    "paragraph",
                            },
                        )
                    )

                    if caption_type == "table":

                        pending_table_caption = (
                            self._normalize_inline_text(
                                stripped
                            )
                        )

                    else:

                        pending_table_caption = (
                            None
                        )

                    i += 1
                    continue

            # ==================================================
            # NORMAL PARAGRAPH
            #
            # Gather contiguous text lines until another
            # structural Markdown block starts.
            # ==================================================

            list_indent_stack.clear()
            pending_table_caption = None

            paragraph_lines: list[str] = [
                stripped
            ]

            i += 1

            while i < len(lines):

                candidate = lines[i]

                if not candidate.strip():
                    break

                if self._starts_structural_block(
                    lines,
                    i,
                ):
                    break

                paragraph_lines.append(
                    candidate.strip()
                )

                i += 1

            paragraph_content = (
                self._normalize_inline_text(
                    " ".join(
                        paragraph_lines
                    )
                )
            )

            if paragraph_content:

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type=
                            "paragraph",

                        content=
                            paragraph_content,

                        page_number=
                            None,

                        section_title=
                            self._section_title(
                                heading_path
                            ),

                        heading_path=
                            heading_path,

                        metadata={},
                    )
                )

        return elements

    # ==========================================================
    # STRUCTURAL BLOCK DETECTION
    # ==========================================================

    def _starts_structural_block(
        self,
        lines: list[str],
        index: int,
    ) -> bool:

        if index >= len(lines):
            return False

        line = lines[index]

        if ATX_HEADING_PATTERN.match(
            line
        ):
            return True

        if FENCE_PATTERN.match(
            line
        ):
            return True

        if LIST_ITEM_PATTERN.match(
            line
        ):
            return True

        if self._is_table_start(
            lines,
            index,
        ):
            return True

        if (
            index + 1 < len(lines)
            and
            line.strip()
            and
            SETEXT_HEADING_PATTERN.match(
                lines[index + 1]
            )
        ):
            return True

        return False

    # ==========================================================
    # HEADING STACK
    # ==========================================================

    def _update_heading_stack(
        self,
        heading_stack: dict[
            int,
            str,
        ],
        *,
        level: int,
        title: str,
    ) -> None:

        for existing_level in list(
            heading_stack
        ):

            if existing_level >= level:

                heading_stack.pop(
                    existing_level,
                    None,
                )

        heading_stack[
            level
        ] = title

    # ==========================================================
    # BUILD HEADING PATH
    # ==========================================================

    def _build_heading_path(
        self,
        heading_stack: dict[
            int,
            str,
        ],
    ) -> list[str]:

        return [
            heading_stack[level]

            for level in sorted(
                heading_stack
            )
        ]

    # ==========================================================
    # SECTION TITLE
    # ==========================================================

    def _section_title(
        self,
        heading_path: list[str],
    ) -> str | None:

        if not heading_path:
            return None

        return heading_path[-1]

    # ==========================================================
    # CAPTION TYPE
    # ==========================================================

    def _get_caption_type(
        self,
        text: str,
    ) -> str | None:

        normalized = (
            self._normalize_inline_text(
                text
            )
        )

        if TABLE_CAPTION_PATTERN.match(
            normalized
        ):
            return "table"

        if FIGURE_CAPTION_PATTERN.match(
            normalized
        ):
            return "figure"

        return None

    # ==========================================================
    # MARKDOWN TABLE START
    # ==========================================================

    def _is_table_start(
        self,
        lines: list[str],
        index: int,
    ) -> bool:

        if (
            index < 0
            or
            index + 1 >= len(lines)
        ):
            return False

        header_line = lines[index]
        separator_line = lines[
            index + 1
        ]

        if "|" not in header_line:
            return False

        if "|" not in separator_line:
            return False

        separator_cells = (
            self._split_table_row(
                separator_line
            )
        )

        if not separator_cells:
            return False

        # Every separator cell must look like:
        #
        # ---
        # :---
        # ---:
        # :---:
        #
        if not all(
            TABLE_SEPARATOR_CELL_PATTERN.fullmatch(
                cell.strip()
            )
            for cell
            in separator_cells
        ):
            return False

        header_cells = (
            self._split_table_row(
                header_line
            )
        )

        if not header_cells:
            return False

        return (
            len(header_cells)
            == len(separator_cells)
        )

    # ==========================================================
    # SPLIT MARKDOWN TABLE ROW
    # ==========================================================

    def _split_table_row(
        self,
        line: str,
    ) -> list[str]:
        """
        Split one Markdown table row while respecting:

        - escaped pipes:
            \\|

        - inline-code pipes:
            `a|b`

        Outer pipes are optional.
        """

        value = line.strip()

        if not value:
            return []

        if value.startswith("|"):
            value = value[1:]

        if (
            value.endswith("|")
            and
            not value.endswith(r"\|")
        ):
            value = value[:-1]

        cells: list[str] = []

        buffer: list[str] = []

        i = 0

        code_delimiter: str | None = None

        while i < len(value):

            char = value[i]

            # --------------------------------------------------
            # Escaped pipe:
            #
            # \|
            # --------------------------------------------------

            if (
                char == "\\"
                and
                i + 1 < len(value)
                and
                value[i + 1] == "|"
            ):

                buffer.append("|")
                i += 2
                continue

            # --------------------------------------------------
            # Inline-code delimiter.
            #
            # Supports `code` and ``code`` sufficiently for table
            # splitting.
            # --------------------------------------------------

            if char == "`":

                run_start = i

                while (
                    i < len(value)
                    and
                    value[i] == "`"
                ):
                    i += 1

                delimiter = value[
                    run_start:i
                ]

                if code_delimiter is None:

                    code_delimiter = (
                        delimiter
                    )

                elif (
                    delimiter
                    == code_delimiter
                ):

                    code_delimiter = None

                buffer.append(
                    delimiter
                )

                continue

            if (
                char == "|"
                and
                code_delimiter is None
            ):

                cells.append(
                    self._normalize_table_cell(
                        "".join(
                            buffer
                        )
                    )
                )

                buffer.clear()

                i += 1
                continue

            buffer.append(
                char
            )

            i += 1

        cells.append(
            self._normalize_table_cell(
                "".join(
                    buffer
                )
            )
        )

        return cells

    # ==========================================================
    # TABLE ALIGNMENTS
    # ==========================================================

    def _parse_table_alignments(
        self,
        separator_cells: list[str],
    ) -> list[str]:

        alignments: list[str] = []

        for cell in separator_cells:

            value = cell.strip()

            left = value.startswith(":")
            right = value.endswith(":")

            if left and right:
                alignment = "center"

            elif right:
                alignment = "right"

            elif left:
                alignment = "left"

            else:
                alignment = "default"

            alignments.append(
                alignment
            )

        return alignments

    # ==========================================================
    # NORMALIZE TABLE ROW WIDTHS
    # ==========================================================

    def _normalize_table_rows(
        self,
        rows: list[
            list[str]
        ],
    ) -> list[
        list[str]
    ]:

        if not rows:
            return []

        width = max(
            len(row)
            for row in rows
        )

        if width == 0:
            return []

        return [
            row
            + (
                [""] *
                (
                    width
                    - len(row)
                )
            )

            for row in rows
        ]

    # ==========================================================
    # REMOVE GLOBALLY EMPTY TABLE COLUMNS
    # ==========================================================

    def _remove_empty_columns(
        self,
        rows: list[
            list[str]
        ],
    ) -> list[
        list[str]
    ]:

        if not rows:
            return []

        width = max(
            len(row)
            for row in rows
        )

        padded_rows = [
            row
            + (
                [""] *
                (
                    width
                    - len(row)
                )
            )

            for row in rows
        ]

        columns_to_keep: list[int] = []

        for column_index in range(
            width
        ):

            if any(
                row[
                    column_index
                ].strip()

                for row
                in padded_rows
            ):

                columns_to_keep.append(
                    column_index
                )

        if not columns_to_keep:
            return []

        return [
            [
                row[
                    column_index
                ]

                for column_index
                in columns_to_keep
            ]

            for row
            in padded_rows
        ]

    # ==========================================================
    # TABLE TO NORMALIZED MARKDOWN
    # ==========================================================

    def _table_to_markdown(
        self,
        rows: list[
            list[str]
        ],
    ) -> str:

        if not rows:
            return ""

        column_count = max(
            len(row)
            for row in rows
        )

        normalized_rows = [
            row
            + (
                [""] *
                (
                    column_count
                    - len(row)
                )
            )

            for row in rows
        ]

        escaped_rows = [
            [
                self._escape_markdown_cell(
                    cell
                )

                for cell
                in row
            ]

            for row
            in normalized_rows
        ]

        lines: list[str] = []

        lines.append(
            "| "
            + " | ".join(
                escaped_rows[0]
            )
            + " |"
        )

        lines.append(
            "| "
            + " | ".join(
                "---"
                for _ in range(
                    column_count
                )
            )
            + " |"
        )

        for row in (
            escaped_rows[1:]
        ):

            lines.append(
                "| "
                + " | ".join(
                    row
                )
                + " |"
            )

        return "\n".join(
            lines
        )

    # ==========================================================
    # LIST LEVEL
    # ==========================================================

    def _get_list_level(
        self,
        indent_width: int,
        indent_stack: list[int],
    ) -> int:
        """
        Infer nested list depth from observed indentation.

        Example:

        - Item A             indent 0 -> level 0
          - Item B           indent 2 -> level 1
            - Item C         indent 4 -> level 2

        We do not assume Markdown authors always use exactly
        four spaces.
        """

        if not indent_stack:

            indent_stack.append(
                indent_width
            )

            return 0

        # Deeper indentation.
        if (
            indent_width
            > indent_stack[-1]
        ):

            indent_stack.append(
                indent_width
            )

            return (
                len(
                    indent_stack
                )
                - 1
            )

        # Return to an existing indentation level.
        while (
            len(indent_stack) > 1
            and
            indent_width
            < indent_stack[-1]
        ):

            indent_stack.pop()

        if (
            indent_width
            == indent_stack[-1]
        ):

            return (
                len(
                    indent_stack
                )
                - 1
            )

        # Irregular indentation that does not exactly match a
        # previous level. Treat it as a new child level rather
        # than losing the structural hint.

        if (
            indent_width
            > indent_stack[-1]
        ):

            indent_stack.append(
                indent_width
            )

        else:

            indent_stack[-1] = (
                indent_width
            )

        return (
            len(
                indent_stack
            )
            - 1
        )

    # ==========================================================
    # INDENT WIDTH
    # ==========================================================

    def _indent_width(
        self,
        value: str,
    ) -> int:

        width = 0

        for char in value:

            if char == "\t":
                width += 4

            else:
                width += 1

        return width

    # ==========================================================
    # LEADING INDENT WIDTH
    # ==========================================================

    def _leading_indent_width(
        self,
        value: str,
    ) -> int:

        prefix: list[str] = []

        for char in value:

            if char not in (
                " ",
                "\t",
            ):
                break

            prefix.append(
                char
            )

        return self._indent_width(
            "".join(
                prefix
            )
        )

    # ==========================================================
    # CLOSING CODE FENCE
    # ==========================================================

    def _is_closing_fence(
        self,
        line: str,
        *,
        fence_char: str,
        minimum_length: int,
    ) -> bool:

        stripped = line.strip()

        if not stripped:
            return False

        if (
            any(
                char != fence_char
                for char in stripped
            )
        ):
            return False

        return (
            len(stripped)
            >= minimum_length
        )

    # ==========================================================
    # NEXT NONBLANK INDEX
    # ==========================================================

    def _next_nonblank_index(
        self,
        lines: list[str],
        start: int,
    ) -> int | None:

        for index in range(
            start,
            len(lines),
        ):

            if lines[index].strip():

                return index

        return None

    # ==========================================================
    # TABLE CELL NORMALIZATION
    # ==========================================================

    def _normalize_table_cell(
        self,
        value: str,
    ) -> str:

        value = value.strip()

        value = re.sub(
            r"[ \t]+",
            " ",
            value,
        )

        return value

    # ==========================================================
    # MARKDOWN TABLE CELL ESCAPING
    # ==========================================================

    def _escape_markdown_cell(
        self,
        value: str,
    ) -> str:

        value = (
            self._normalize_table_cell(
                value
            )
        )

        return value.replace(
            "|",
            r"\|",
        )

    # ==========================================================
    # INLINE TEXT NORMALIZATION
    # ==========================================================

    def _normalize_inline_text(
        self,
        value: str,
    ) -> str:

        value = (
            str(value)
            .replace(
                "\xa0",
                " ",
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