from __future__ import annotations

import re

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


UNDERLINE_HEADING_PATTERN = re.compile(
    r"^ {0,3}(?P<marker>=+|-+)[ \t]*$"
)

BULLET_LIST_PATTERN = re.compile(
    r"^"
    r"(?P<indent>[ \t]*)"
    r"(?P<marker>[-+*•▪◦])"
    r"[ \t]+"
    r"(?P<content>\S.*)"
    r"$"
)

NUMBERED_LIST_PATTERN = re.compile(
    r"^"
    r"(?P<indent>[ \t]*)"
    r"(?P<marker>\d+[.)])"
    r"[ \t]+"
    r"(?P<content>\S.*)"
    r"$"
)

NUMBERED_SECTION_PATTERN = re.compile(
    r"^"
    r"(?P<number>\d+(?:\.\d+)*)"
    r"[.)]?"
    r"\s+"
    r"(?P<title>\S.*)"
    r"$"
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

KEY_VALUE_PATTERN = re.compile(
    r"^"
    r"(?P<key>[A-Za-z][A-Za-z0-9 /&()_-]{1,60})"
    r":"
    r"[ \t]+"
    r"(?P<value>\S.*)"
    r"$"
)

FIXED_SEPARATOR_GROUP_PATTERN = re.compile(
    r"-{3,}"
)

PIPE_SEPARATOR_CELL_PATTERN = re.compile(
    r"^:?-{3,}:?$"
)


class TextParser(BaseParser):
    """
    Conservative structure-aware parser for plain-text documents.

    TXT has no formal semantic structure, so this parser only
    recognizes high-confidence patterns:

    - underlined headings
    - conservative numbered headings
    - short all-caps headings
    - bullet and numbered lists
    - nested list indentation
    - key/value lines
    - fixed-width ASCII tables
    - pipe-style tables
    - table/figure captions
    - normal prose paragraphs

    When structure is ambiguous, content is preserved as a normal
    paragraph instead of aggressively guessing.

    Page numbers are None because plain text has no reliable
    pagination model.
    """

    supported_extensions = {
        "txt",
        "text",
    }

    parser_version = "2.0"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:
        text = self._decode_text(content)

        text = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
        )

        lines = text.split("\n")

        elements: list[ParsedElement] = []

        heading_stack: dict[int, str] = {}
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
                list_indent_stack.clear()

                # Do not clear pending_table_caption here.
                # A caption can legitimately be separated from
                # its table by one or more blank lines.

                i += 1
                continue

            # ==================================================
            # UNDERLINED HEADING
            #
            # SECURITY POLICY
            # ===============
            #
            # Encryption
            # ----------
            # ==================================================

            if (
                i + 1 < len(lines)
                and self._is_underlined_heading(
                    lines,
                    i,
                )
            ):
                title = self._normalize_inline_text(
                    stripped
                )

                underline_match = (
                    UNDERLINE_HEADING_PATTERN.match(
                        lines[i + 1]
                    )
                )

                assert underline_match is not None

                marker = underline_match.group(
                    "marker"
                )

                heading_level = (
                    1
                    if marker.startswith("=")
                    else 2
                )

                caption_type = self._get_caption_type(
                    title
                )

                if caption_type is not None:
                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type="caption",
                            content=title,
                            page_number=None,
                            section_title=self._section_title(
                                heading_path
                            ),
                            heading_path=heading_path,
                            metadata={
                                "caption_type":
                                    caption_type,
                                "source_syntax":
                                    "underlined_heading",
                            },
                        )
                    )

                    pending_table_caption = (
                        title
                        if caption_type == "table"
                        else None
                    )

                else:
                    pending_table_caption = None

                    self._update_heading_stack(
                        heading_stack,
                        level=heading_level,
                        title=title,
                    )

                    heading_path = (
                        self._build_heading_path(
                            heading_stack
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type="heading",
                            content=title,
                            page_number=None,
                            section_title=title,
                            heading_path=heading_path,
                            metadata={
                                "heading_level":
                                    heading_level,
                                "source_syntax":
                                    "underline",
                            },
                        )
                    )

                list_indent_stack.clear()

                i += 2
                continue

            # ==================================================
            # CAPTION
            #
            # Plain-text table captions are only accepted when
            # the next nonblank block is really a table.
            #
            # This avoids misclassifying prose such as:
            #
            # "Table 2 contains the results."
            # ==================================================

            caption_type = self._get_caption_type(
                stripped
            )

            if (
                caption_type is not None
                and (
                    caption_type == "figure"
                    or self._next_nonblank_is_table(
                        lines,
                        i + 1,
                    )
                )
            ):
                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                normalized_caption = (
                    self._normalize_inline_text(
                        stripped
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type="caption",
                        content=normalized_caption,
                        page_number=None,
                        section_title=self._section_title(
                            heading_path
                        ),
                        heading_path=heading_path,
                        metadata={
                            "caption_type":
                                caption_type,
                            "source_syntax":
                                "paragraph",
                        },
                    )
                )

                pending_table_caption = (
                    normalized_caption
                    if caption_type == "table"
                    else None
                )

                list_indent_stack.clear()

                i += 1
                continue

            # ==================================================
            # FIXED-WIDTH ASCII TABLE
            #
            # Control              Status       Evidence
            # -------------------  -----------  ---------------
            # Encryption           Active       AES-256
            # ==================================================

            if self._is_fixed_table_start(
                lines,
                i,
            ):
                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                rows, next_index = (
                    self._parse_fixed_table(
                        lines,
                        i,
                    )
                )

                rows = self._remove_empty_columns(
                    rows
                )

                if rows:
                    column_count = max(
                        len(row)
                        for row in rows
                    )

                    metadata = {
                        "table_index":
                            table_index,
                        "row_count":
                            len(rows),
                        "column_count":
                            column_count,
                        "is_continuation":
                            False,
                        "table_format":
                            "fixed_width",
                    }

                    if pending_table_caption:
                        metadata[
                            "caption"
                        ] = pending_table_caption

                    elements.append(
                        ParsedElement(
                            element_type="table",
                            content=self._table_to_markdown(
                                rows
                            ),
                            page_number=None,
                            section_title=self._section_title(
                                heading_path
                            ),
                            heading_path=heading_path,
                            metadata=metadata,
                        )
                    )

                    table_index += 1

                pending_table_caption = None
                list_indent_stack.clear()

                i = next_index
                continue

            # ==================================================
            # PIPE-STYLE TABLE
            #
            # | Control | Status |
            # | --- | --- |
            # | MFA | Active |
            # ==================================================

            if self._is_pipe_table_start(
                lines,
                i,
            ):
                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                rows, next_index = (
                    self._parse_pipe_table(
                        lines,
                        i,
                    )
                )

                rows = self._remove_empty_columns(
                    rows
                )

                if rows:
                    column_count = max(
                        len(row)
                        for row in rows
                    )

                    metadata = {
                        "table_index":
                            table_index,
                        "row_count":
                            len(rows),
                        "column_count":
                            column_count,
                        "is_continuation":
                            False,
                        "table_format":
                            "pipe",
                    }

                    if pending_table_caption:
                        metadata[
                            "caption"
                        ] = pending_table_caption

                    elements.append(
                        ParsedElement(
                            element_type="table",
                            content=self._table_to_markdown(
                                rows
                            ),
                            page_number=None,
                            section_title=self._section_title(
                                heading_path
                            ),
                            heading_path=heading_path,
                            metadata=metadata,
                        )
                    )

                    table_index += 1

                pending_table_caption = None
                list_indent_stack.clear()

                i = next_index
                continue

            # ==================================================
            # CONSERVATIVE NUMBERED HEADING
            #
            # Strong examples:
            #
            # 2. SECURITY CONTROLS
            # 2.1 Encryption and Key Management
            #
            # Weak examples remain list items:
            #
            # 1. Require MFA for administrators.
            # ==================================================

            numbered_section_match = (
                NUMBERED_SECTION_PATTERN.match(
                    stripped
                )
            )

            if (
                numbered_section_match is not None
                and self._is_numbered_heading(
                    numbered_section_match,
                    lines,
                    i,
                )
            ):
                title = self._normalize_inline_text(
                    stripped
                )

                number = (
                    numbered_section_match.group(
                        "number"
                    )
                )

                depth = len(
                    number.split(".")
                )

                heading_level = min(
                    6,
                    depth,
                )

                pending_table_caption = None
                list_indent_stack.clear()

                self._update_heading_stack(
                    heading_stack,
                    level=heading_level,
                    title=title,
                )

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type="heading",
                        content=title,
                        page_number=None,
                        section_title=title,
                        heading_path=heading_path,
                        metadata={
                            "heading_level":
                                heading_level,
                            "source_syntax":
                                "numbered",
                        },
                    )
                )

                i += 1
                continue

            # ==================================================
            # SHORT ALL-CAPS HEADING
            #
            # Conservative fallback only.
            # ==================================================

            if self._is_all_caps_heading(
                lines,
                i,
            ):
                title = self._normalize_inline_text(
                    stripped
                )

                heading_level = (
                    1
                    if not heading_stack
                    else 2
                )

                pending_table_caption = None
                list_indent_stack.clear()

                self._update_heading_stack(
                    heading_stack,
                    level=heading_level,
                    title=title,
                )

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type="heading",
                        content=title,
                        page_number=None,
                        section_title=title,
                        heading_path=heading_path,
                        metadata={
                            "heading_level":
                                heading_level,
                            "source_syntax":
                                "all_caps",
                        },
                    )
                )

                i += 1
                continue

            # ==================================================
            # LIST ITEM
            # ==================================================

            list_match = (
                BULLET_LIST_PATTERN.match(
                    raw_line
                )
                or
                NUMBERED_LIST_PATTERN.match(
                    raw_line
                )
            )

            if list_match is not None:
                pending_table_caption = None

                indent_width = self._indent_width(
                    list_match.group(
                        "indent"
                    )
                )

                list_level = self._get_list_level(
                    indent_width,
                    list_indent_stack,
                )

                marker = list_match.group(
                    "marker"
                )

                list_kind = (
                    "bullet"
                    if marker[0]
                    in "-+*•▪◦"
                    else "numbered"
                )

                item_lines = [
                    list_match.group(
                        "content"
                    ).strip()
                ]

                j = i + 1

                # Preserve indented continuation lines that
                # belong to the same list item.

                while j < len(lines):
                    continuation = lines[j]

                    if not continuation.strip():
                        break

                    if (
                        BULLET_LIST_PATTERN.match(
                            continuation
                        )
                        or
                        NUMBERED_LIST_PATTERN.match(
                            continuation
                        )
                    ):
                        break

                    if self._starts_structural_block(
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
                        <= indent_width
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

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type="list_item",
                        content=item_content,
                        page_number=None,
                        section_title=self._section_title(
                            heading_path
                        ),
                        heading_path=heading_path,
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
            # KEY / VALUE LINE
            #
            # Retention: 30 days
            #
            # It remains a paragraph so the existing chunker does
            # not need special logic, but metadata preserves the
            # semantic hint.
            # ==================================================

            key_value_match = (
                KEY_VALUE_PATTERN.match(
                    stripped
                )
            )

            if key_value_match is not None:
                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                elements.append(
                    ParsedElement(
                        element_type="paragraph",
                        content=self._normalize_inline_text(
                            stripped
                        ),
                        page_number=None,
                        section_title=self._section_title(
                            heading_path
                        ),
                        heading_path=heading_path,
                        metadata={
                            "text_role":
                                "key_value",
                            "key":
                                key_value_match.group(
                                    "key"
                                ).strip(),
                        },
                    )
                )

                pending_table_caption = None
                list_indent_stack.clear()

                i += 1
                continue

            # ==================================================
            # NORMAL PARAGRAPH
            #
            # Wrapped prose lines are joined until a blank line or
            # high-confidence structural boundary is encountered.
            # ==================================================

            pending_table_caption = None
            list_indent_stack.clear()

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

                if KEY_VALUE_PATTERN.match(
                    candidate.strip()
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
                        element_type="paragraph",
                        content=paragraph_content,
                        page_number=None,
                        section_title=self._section_title(
                            heading_path
                        ),
                        heading_path=heading_path,
                        metadata={},
                    )
                )

        return elements

    # ==========================================================
    # DECODING
    # ==========================================================

    def _decode_text(
        self,
        content: bytes,
    ) -> str:
        """
        Support the most common plain-text encodings without an
        external dependency.
        """

        if content.startswith(
            (
                b"\xff\xfe",
                b"\xfe\xff",
            )
        ):
            return content.decode(
                "utf-16"
            )

        if content.startswith(
            b"\xef\xbb\xbf"
        ):
            return content.decode(
                "utf-8-sig"
            )

        try:
            return content.decode(
                "utf-8"
            )

        except UnicodeDecodeError:
            return content.decode(
                "cp1252",
                errors="replace",
            )

    # ==========================================================
    # UNDERLINED HEADING
    # ==========================================================

    def _is_underlined_heading(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        if index + 1 >= len(lines):
            return False

        title = lines[index].strip()

        if not title:
            return False

        if len(title) > 160:
            return False

        if "|" in title:
            return False

        underline = lines[
            index + 1
        ]

        match = (
            UNDERLINE_HEADING_PATTERN.match(
                underline
            )
        )

        if match is None:
            return False

        # A fixed-width table separator has multiple dash groups,
        # so it must not be mistaken for a heading underline.

        if self._fixed_separator_spans(
            underline
        ):
            return False

        return True

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
    # NEXT NONBLANK BLOCK
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

    def _next_nonblank_is_table(
        self,
        lines: list[str],
        start: int,
    ) -> bool:
        index = self._next_nonblank_index(
            lines,
            start,
        )

        if index is None:
            return False

        return (
            self._is_fixed_table_start(
                lines,
                index,
            )
            or
            self._is_pipe_table_start(
                lines,
                index,
            )
        )

    # ==========================================================
    # FIXED-WIDTH TABLE DETECTION
    # ==========================================================

    def _fixed_separator_spans(
        self,
        line: str,
    ) -> list[
        tuple[int, int]
    ]:
        matches = list(
            FIXED_SEPARATOR_GROUP_PATTERN.finditer(
                line
            )
        )

        if len(matches) < 2:
            return []

        # Require at least two spaces between column separator
        # groups. This distinguishes a table separator from one
        # long underline.

        for left, right in zip(
            matches,
            matches[1:],
        ):
            between = line[
                left.end():
                right.start()
            ]

            if (
                len(between) < 2
                or
                not between.isspace()
            ):
                return []

        return [
            (
                match.start(),
                match.end(),
            )
            for match
            in matches
        ]

    def _is_fixed_table_start(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        if index + 1 >= len(lines):
            return False

        if not lines[index].strip():
            return False

        spans = self._fixed_separator_spans(
            lines[index + 1]
        )

        if len(spans) < 2:
            return False

        starts = [
            start
            for start, _
            in spans
        ]

        header_cells = self._slice_fixed_row(
            lines[index],
            starts,
        )

        return (
            sum(
                bool(cell.strip())
                for cell
                in header_cells
            )
            >= 2
        )

    def _slice_fixed_row(
        self,
        line: str,
        starts: list[int],
    ) -> list[str]:
        cells: list[str] = []

        for cell_index, start in enumerate(
            starts
        ):
            end = (
                starts[
                    cell_index + 1
                ]
                if (
                    cell_index + 1
                    < len(starts)
                )
                else len(line)
            )

            cells.append(
                line[
                    start:end
                ].strip()
            )

        return cells

    def _parse_fixed_table(
        self,
        lines: list[str],
        index: int,
    ) -> tuple[
        list[list[str]],
        int,
    ]:
        spans = self._fixed_separator_spans(
            lines[index + 1]
        )

        starts = [
            start
            for start, _
            in spans
        ]

        rows: list[list[str]] = [
            self._slice_fixed_row(
                lines[index],
                starts,
            )
        ]

        i = index + 2

        while i < len(lines):
            line = lines[i]

            if not line.strip():
                break

            if self._starts_obvious_heading(
                lines,
                i,
            ):
                break

            row = self._slice_fixed_row(
                line,
                starts,
            )

            if not any(
                cell.strip()
                for cell
                in row
            ):
                break

            rows.append(
                row
            )

            i += 1

        return rows, i

    # ==========================================================
    # PIPE TABLE
    # ==========================================================

    def _is_pipe_table_start(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        if index + 1 >= len(lines):
            return False

        if (
            "|" not in lines[index]
            or
            "|" not in lines[
                index + 1
            ]
        ):
            return False

        header_cells = self._split_pipe_row(
            lines[index]
        )

        separator_cells = self._split_pipe_row(
            lines[index + 1]
        )

        if (
            not header_cells
            or
            len(header_cells)
            != len(separator_cells)
        ):
            return False

        return all(
            PIPE_SEPARATOR_CELL_PATTERN.fullmatch(
                cell.strip()
            )
            is not None

            for cell
            in separator_cells
        )

    def _split_pipe_row(
        self,
        line: str,
    ) -> list[str]:
        value = line.strip()

        if value.startswith("|"):
            value = value[1:]

        if (
            value.endswith("|")
            and
            not value.endswith(
                r"\|"
            )
        ):
            value = value[:-1]

        cells: list[str] = []
        buffer: list[str] = []

        escaped = False

        for char in value:
            if escaped:
                if char == "|":
                    buffer.append("|")
                else:
                    buffer.extend(
                        [
                            "\\",
                            char,
                        ]
                    )

                escaped = False
                continue

            if char == "\\":
                escaped = True
                continue

            if char == "|":
                cells.append(
                    self._normalize_inline_text(
                        "".join(
                            buffer
                        )
                    )
                )

                buffer.clear()
                continue

            buffer.append(
                char
            )

        if escaped:
            buffer.append("\\")

        cells.append(
            self._normalize_inline_text(
                "".join(
                    buffer
                )
            )
        )

        return cells

    def _parse_pipe_table(
        self,
        lines: list[str],
        index: int,
    ) -> tuple[
        list[list[str]],
        int,
    ]:
        rows: list[list[str]] = [
            self._split_pipe_row(
                lines[index]
            )
        ]

        i = index + 2

        while i < len(lines):
            line = lines[i]

            if not line.strip():
                break

            if "|" not in line:
                break

            rows.append(
                self._split_pipe_row(
                    line
                )
            )

            i += 1

        return rows, i

    # ==========================================================
    # NUMBERED HEADING
    # ==========================================================

    def _is_numbered_heading(
        self,
        match: re.Match[str],
        lines: list[str],
        index: int,
    ) -> bool:
        full_text = lines[index].strip()

        title = match.group(
            "title"
        ).strip()

        number = match.group(
            "number"
        )

        # Navigation entries are not headings.
        if "..." in full_text:
            return False

        if len(full_text) > 120:
            return False

        if len(title.split()) > 12:
            return False

        if title.endswith(
            (
                ".",
                "?",
                "!",
                ";",
                ":",
            )
        ):
            return False

        # 2.1 / 3.2.4 is strong section evidence.
        if "." in number:
            return True

        # For a top-level numeric section, require the title itself
        # to be all caps. This avoids mistaking ordinary numbered
        # instructions for section headings.

        letters = [
            char
            for char
            in title
            if char.isalpha()
        ]

        return bool(
            letters
            and
            all(
                char.isupper()
                for char
                in letters
            )
        )

    # ==========================================================
    # ALL-CAPS HEADING
    # ==========================================================

    def _is_all_caps_heading(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        value = lines[index].strip()

        if not value:
            return False

        if len(value) > 100:
            return False

        word_count = len(
            value.split()
        )

        if not (
            2
            <= word_count
            <= 12
        ):
            return False

        if (
            "..." in value
            or
            ":" in value
            or
            "|" in value
        ):
            return False

        if value.endswith(
            (
                ".",
                "?",
                "!",
                ";",
            )
        ):
            return False

        if (
            BULLET_LIST_PATTERN.match(
                lines[index]
            )
            or
            NUMBERED_LIST_PATTERN.match(
                lines[index]
            )
        ):
            return False

        letters = [
            char
            for char
            in value
            if char.isalpha()
        ]

        if not letters:
            return False

        if not all(
            char.isupper()
            for char
            in letters
        ):
            return False

        previous_blank = (
            index == 0
            or
            not lines[
                index - 1
            ].strip()
        )

        next_blank = (
            index + 1
            >= len(lines)
            or
            not lines[
                index + 1
            ].strip()
        )

        return (
            previous_blank
            or
            next_blank
        )

    # ==========================================================
    # STRUCTURAL BOUNDARY
    # ==========================================================

    def _starts_obvious_heading(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        if (
            index + 1 < len(lines)
            and
            self._is_underlined_heading(
                lines,
                index,
            )
        ):
            return True

        numbered_match = (
            NUMBERED_SECTION_PATTERN.match(
                lines[index].strip()
            )
        )

        if (
            numbered_match is not None
            and
            self._is_numbered_heading(
                numbered_match,
                lines,
                index,
            )
        ):
            return True

        return self._is_all_caps_heading(
            lines,
            index,
        )

    def _starts_structural_block(
        self,
        lines: list[str],
        index: int,
    ) -> bool:
        if index >= len(lines):
            return False

        if (
            index + 1 < len(lines)
            and
            self._is_underlined_heading(
                lines,
                index,
            )
        ):
            return True

        if (
            self._is_fixed_table_start(
                lines,
                index,
            )
            or
            self._is_pipe_table_start(
                lines,
                index,
            )
        ):
            return True

        if (
            BULLET_LIST_PATTERN.match(
                lines[index]
            )
            or
            NUMBERED_LIST_PATTERN.match(
                lines[index]
            )
        ):
            return True

        numbered_match = (
            NUMBERED_SECTION_PATTERN.match(
                lines[index].strip()
            )
        )

        if (
            numbered_match is not None
            and
            self._is_numbered_heading(
                numbered_match,
                lines,
                index,
            )
        ):
            return True

        if self._is_all_caps_heading(
            lines,
            index,
        ):
            return True

        caption_type = self._get_caption_type(
            lines[index].strip()
        )

        if caption_type == "figure":
            return True

        if (
            caption_type == "table"
            and
            self._next_nonblank_is_table(
                lines,
                index + 1,
            )
        ):
            return True

        return False

    # ==========================================================
    # TABLE NORMALIZATION
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
            for row
            in rows
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

            for row
            in rows
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

    def _table_to_markdown(
        self,
        rows: list[
            list[str]
        ],
    ) -> str:
        if not rows:
            return ""

        width = max(
            len(row)
            for row
            in rows
        )

        normalized_rows = [
            row
            + (
                [""] *
                (
                    width
                    - len(row)
                )
            )

            for row
            in rows
        ]

        escaped_rows = [
            [
                self._normalize_inline_text(
                    cell
                ).replace(
                    "|",
                    r"\|",
                )

                for cell
                in row
            ]

            for row
            in normalized_rows
        ]

        lines = [
            "| "
            + " | ".join(
                escaped_rows[0]
            )
            + " |",

            "| "
            + " | ".join(
                "---"
                for _
                in range(width)
            )
            + " |",
        ]

        for row in escaped_rows[
            1:
        ]:
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
    # HEADING PATH
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

        heading_stack[level] = title

    def _build_heading_path(
        self,
        heading_stack: dict[
            int,
            str,
        ],
    ) -> list[str]:
        return [
            heading_stack[level]

            for level
            in sorted(
                heading_stack
            )
        ]

    def _section_title(
        self,
        heading_path: list[str],
    ) -> str | None:
        if not heading_path:
            return None

        return heading_path[-1]

    # ==========================================================
    # LIST LEVEL
    # ==========================================================

    def _get_list_level(
        self,
        indent_width: int,
        indent_stack: list[int],
    ) -> int:
        if not indent_stack:
            indent_stack.append(
                indent_width
            )

            return 0

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

    def _indent_width(
        self,
        value: str,
    ) -> int:
        return sum(
            4
            if char == "\t"
            else 1

            for char
            in value
        )

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
    # TEXT NORMALIZATION
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
                "\u200b",
                "",
            )
            .replace(
                "\ufeff",
                "",
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
