from __future__ import annotations

from io import BytesIO
import re
from typing import Iterator

from docx import Document
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


HEADING_STYLE_PATTERN = re.compile(
    r"^heading\s+(\d+)$",
    re.IGNORECASE,
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


class DOCXParser(BaseParser):
    """
    Structure-aware Microsoft Word .docx parser.

    Goals:

    - preserve paragraphs and tables in actual body order
    - preserve heading hierarchy
    - recognize Word list paragraphs
    - preserve tables as Markdown
    - preserve Word captions
    - associate a table caption with the following table
    - avoid pretending DOCX page numbers are reliable

    DOCX pagination depends on Word's layout engine, fonts,
    margins, printer configuration, etc., so page_number is
    intentionally None.
    """

    supported_extensions = {
        "docx",
    }

    parser_version = "1.0"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        document = Document(
            BytesIO(content)
        )

        elements: list[
            ParsedElement
        ] = []

        # ------------------------------------------------------
        # Heading hierarchy:
        #
        # {
        #     1: "Security Policy",
        #     2: "Encryption",
        #     3: "Key Management",
        # }
        #
        # becomes:
        #
        # [
        #     "Security Policy",
        #     "Encryption",
        #     "Key Management",
        # ]
        # ------------------------------------------------------

        heading_stack: dict[
            int,
            str,
        ] = {}

        pending_table_caption: str | None = None

        table_index = 0

        for block in self._iter_block_items(
            document
        ):

            # ==================================================
            # PARAGRAPH
            # ==================================================

            if isinstance(
                block,
                Paragraph,
            ):

                text = self._normalize_text(
                    block.text
                )

                if not text:
                    continue

                style_name = self._style_name(
                    block
                )

                # ==============================================
                # HEADING
                # ==============================================

                heading_level = (
                    self._get_heading_level(
                        style_name
                    )
                )

                if heading_level is not None:

                    # Remove lower/deeper stale headings.
                    for level in list(
                        heading_stack
                    ):
                        if level >= heading_level:
                            heading_stack.pop(
                                level,
                                None,
                            )

                    heading_stack[
                        heading_level
                    ] = text

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
                                text,

                            page_number=
                                None,

                            section_title=
                                text,

                            heading_path=
                                heading_path,

                            metadata={
                                "heading_level":
                                    heading_level,

                                "style_name":
                                    style_name,
                            },
                        )
                    )

                    pending_table_caption = None

                    continue

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                section_title = (
                    heading_path[-1]
                    if heading_path
                    else None
                )

                # ==============================================
                # CAPTION
                #
                # We intentionally require BOTH:
                #
                # 1. Word Caption style
                # 2. recognizable Figure/Table caption text
                #
                # This avoids classifying ordinary prose such as:
                #
                # "Table 2 contains the results..."
                #
                # as a caption.
                # ==============================================

                caption_type = (
                    self._get_caption_type(
                        text=text,
                        style_name=style_name,
                    )
                )

                if caption_type is not None:

                    elements.append(
                        ParsedElement(
                            element_type=
                                "caption",

                            content=
                                text,

                            page_number=
                                None,

                            section_title=
                                section_title,

                            heading_path=
                                heading_path,

                            metadata={
                                "caption_type":
                                    caption_type,

                                "style_name":
                                    style_name,
                            },
                        )
                    )

                    if caption_type == "table":
                        pending_table_caption = (
                            text
                        )
                    else:
                        pending_table_caption = (
                            None
                        )

                    continue

                # ==============================================
                # LIST ITEM
                # ==============================================

                if self._is_list_paragraph(
                    block
                ):

                    list_level = (
                        self._get_list_level(
                            block
                        )
                    )

                    list_kind = (
                        self._get_list_kind(
                            block
                        )
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "list_item",

                            content=
                                text,

                            page_number=
                                None,

                            section_title=
                                section_title,

                            heading_path=
                                heading_path,

                            metadata={
                                "style_name":
                                    style_name,

                                "list_level":
                                    list_level,

                                "list_kind":
                                    list_kind,
                            },
                        )
                    )

                    # A list item between a table caption and a
                    # table means they are no longer adjacent.
                    pending_table_caption = None

                    continue

                # ==============================================
                # NORMAL PARAGRAPH
                # ==============================================

                elements.append(
                    ParsedElement(
                        element_type=
                            "paragraph",

                        content=
                            text,

                        page_number=
                            None,

                        section_title=
                            section_title,

                        heading_path=
                            heading_path,

                        metadata={
                            "style_name":
                                style_name,
                        },
                    )
                )

                # A table caption is associated only with the
                # immediately following table.
                pending_table_caption = None

                continue

            # ==================================================
            # TABLE
            # ==================================================

            if isinstance(
                block,
                Table,
            ):

                table_index += 1

                heading_path = (
                    self._build_heading_path(
                        heading_stack
                    )
                )

                section_title = (
                    heading_path[-1]
                    if heading_path
                    else None
                )

                rows = (
                    self._extract_table_rows(
                        block
                    )
                )

                rows = (
                    self._remove_empty_rows(
                        rows
                    )
                )

                rows = (
                    self._remove_empty_columns(
                        rows
                    )
                )

                # A completely empty/invalid table should not
                # create a useless retrieval element.
                if (
                    not rows
                    or
                    not any(
                        any(
                            cell.strip()
                            for cell
                            in row
                        )
                        for row
                        in rows
                    )
                ):

                    pending_table_caption = None
                    continue

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
                        table_index - 1,

                    "row_count":
                        len(
                            normalized_rows
                        ),

                    "column_count":
                        column_count,

                    "is_continuation":
                        False,
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
                            section_title,

                        heading_path=
                            heading_path,

                        metadata=
                            metadata,
                    )
                )

                pending_table_caption = None

        return elements

    # ==========================================================
    # DOCUMENT BODY ORDER
    # ==========================================================

    def _iter_block_items(
        self,
        document: _Document,
    ) -> Iterator[
        Paragraph | Table
    ]:
        """
        Yield paragraphs and tables in the order they appear in
        the Word document body.

        Using:

            document.paragraphs
            document.tables

        separately would lose their interleaving.

        Example:

            paragraph
            paragraph
            table
            paragraph

        must remain in exactly that order.
        """

        parent_element = (
            document.element.body
        )

        for child in (
            parent_element.iterchildren()
        ):

            if isinstance(
                child,
                CT_P,
            ):

                yield Paragraph(
                    child,
                    document,
                )

            elif isinstance(
                child,
                CT_Tbl,
            ):

                yield Table(
                    child,
                    document,
                )

    # ==========================================================
    # HEADING DETECTION
    # ==========================================================

    def _get_heading_level(
        self,
        style_name: str,
    ) -> int | None:
        """
        Recognize normal Word Heading styles:

            Heading 1
            Heading 2
            Heading 3
            ...

        We deliberately do not infer arbitrary bold paragraphs as
        headings in DOCX v1.0 because Word already provides
        explicit semantic styles.
        """

        match = (
            HEADING_STYLE_PATTERN.match(
                style_name
            )
        )

        if not match:
            return None

        level = int(
            match.group(1)
        )

        # Limit retrieval hierarchy depth to something practical.
        # We still preserve deeper Word headings by mapping them
        # into the same hierarchy rather than dropping them.

        return max(
            1,
            min(
                level,
                9,
            ),
        )

    # ==========================================================
    # HEADING PATH
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
    # STYLE NAME
    # ==========================================================

    def _style_name(
        self,
        paragraph: Paragraph,
    ) -> str:

        try:

            if (
                paragraph.style
                and
                paragraph.style.name
            ):

                return (
                    paragraph
                    .style
                    .name
                    .strip()
                )

        except Exception:
            pass

        return ""

    # ==========================================================
    # CAPTION DETECTION
    # ==========================================================

    def _get_caption_type(
        self,
        *,
        text: str,
        style_name: str,
    ) -> str | None:

        # Require actual Word Caption style.
        if (
            style_name.casefold()
            != "caption"
        ):
            return None

        if TABLE_CAPTION_PATTERN.match(
            text
        ):
            return "table"

        if FIGURE_CAPTION_PATTERN.match(
            text
        ):
            return "figure"

        return None

    # ==========================================================
    # LIST DETECTION
    # ==========================================================

    def _is_list_paragraph(
        self,
        paragraph: Paragraph,
    ) -> bool:
        """
        A Word list may be represented through:

        - numbering properties (numPr)
        - built-in list styles

        We check both.
        """

        p_pr = paragraph._p.pPr

        if (
            p_pr is not None
            and
            p_pr.numPr is not None
        ):
            return True

        style_name = (
            self._style_name(
                paragraph
            )
            .casefold()
        )

        return (
            style_name.startswith(
                "list "
            )
            or
            style_name == "list"
        )

    # ==========================================================
    # LIST LEVEL
    # ==========================================================

    def _get_list_level(
        self,
        paragraph: Paragraph,
    ) -> int:

        p_pr = paragraph._p.pPr

        if (
            p_pr is None
            or
            p_pr.numPr is None
            or
            p_pr.numPr.ilvl is None
        ):
            return 0

        try:

            value = (
                p_pr
                .numPr
                .ilvl
                .val
            )

            return int(
                value
            )

        except (
            TypeError,
            ValueError,
            AttributeError,
        ):

            return 0

    # ==========================================================
    # LIST KIND
    # ==========================================================

    def _get_list_kind(
        self,
        paragraph: Paragraph,
    ) -> str:
        """
        Word numbering definitions can be complex.

        For v1.0 we use the style name when it clearly tells us
        the type.

        Otherwise we preserve the item as a generic list instead
        of inventing numbering/bullet semantics.
        """

        style_name = (
            self._style_name(
                paragraph
            )
            .casefold()
        )

        if "bullet" in style_name:
            return "bullet"

        if (
            "number" in style_name
            or
            "numbered" in style_name
        ):
            return "numbered"

        return "list"

    # ==========================================================
    # TABLE EXTRACTION
    # ==========================================================

    def _extract_table_rows(
        self,
        table: Table,
    ) -> list[
        list[str]
    ]:

        rows: list[
            list[str]
        ] = []

        for row in table.rows:

            values: list[
                str
            ] = []

            for cell in row.cells:

                cell_text = (
                    self._extract_cell_text(
                        cell
                    )
                )

                values.append(
                    cell_text
                )

            rows.append(
                values
            )

        return rows

    # ==========================================================
    # CELL TEXT
    # ==========================================================

    def _extract_cell_text(
        self,
        cell,
    ) -> str:
        """
        Preserve multiple paragraphs inside a Word table cell.

        Example:

            Encryption:
            AES-256

        becomes:

            Encryption:
            AES-256

        rather than silently concatenating words.
        """

        parts: list[
            str
        ] = []

        for paragraph in cell.paragraphs:

            text = (
                self._normalize_text(
                    paragraph.text
                )
            )

            if text:
                parts.append(
                    text
                )

        return " ".join(
            parts
        ).strip()

    # ==========================================================
    # REMOVE EMPTY TABLE ROWS
    # ==========================================================

    def _remove_empty_rows(
        self,
        rows: list[
            list[str]
        ],
    ) -> list[
        list[str]
    ]:

        return [
            row
            for row in rows
            if any(
                cell.strip()
                for cell in row
            )
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
        """
        Same principle used by the PDF parser:

        If an entire physical column is empty across every row,
        remove it.

        This helps with DOCX tables containing spacer columns.
        """

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

        columns_to_keep: list[
            int
        ] = []

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
            for row in padded_rows
        ]

    # ==========================================================
    # TABLE TO MARKDOWN
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
                for cell in row
            ]
            for row in normalized_rows
        ]

        lines: list[
            str
        ] = []

        # First row is represented as the Markdown header.
        #
        # Even if the original Word table technically has no
        # header row, this provides a stable machine-readable
        # structure consistent with our PDF table representation.

        header = (
            "| "
            + " | ".join(
                escaped_rows[0]
            )
            + " |"
        )

        separator = (
            "| "
            + " | ".join(
                "---"
                for _ in range(
                    column_count
                )
            )
            + " |"
        )

        lines.append(
            header
        )

        lines.append(
            separator
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
    # MARKDOWN TABLE CELL ESCAPING
    # ==========================================================

    def _escape_markdown_cell(
        self,
        value: str,
    ) -> str:

        value = (
            self._normalize_text(
                value
            )
        )

        return value.replace(
            "|",
            r"\|",
        )

    # ==========================================================
    # TEXT NORMALIZATION
    # ==========================================================

    def _normalize_text(
        self,
        value: str,
    ) -> str:

        if not value:
            return ""

        value = value.replace(
            "\xa0",
            " ",
        )

        value = value.replace(
            "\r",
            " ",
        )

        value = value.replace(
            "\n",
            " ",
        )

        value = re.sub(
            r"\s+",
            " ",
            value,
        )

        return value.strip()