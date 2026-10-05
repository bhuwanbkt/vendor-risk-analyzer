import re
from statistics import median

import pymupdf

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:\d+|[ivxlcdm]+)$",
    re.IGNORECASE,
)


class PDFParser(BaseParser):
    supported_extensions = {"pdf"}

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        document = pymupdf.open(
            stream=content,
            filetype="pdf",
        )

        try:
            raw_blocks: list[dict] = []

            # ==================================================
            # PASS 1
            # Extract text + tables from every page
            # ==================================================

            for page_index, page in enumerate(
                document
            ):
                page_height = float(
                    page.rect.height
                )

                page_blocks: list[dict] = []

                # ----------------------------------------------
                # First find tables.
                #
                # We do this before text extraction so that
                # table text does not get duplicated later as
                # normal paragraph blocks.
                # ----------------------------------------------

                table_bboxes: list[
                    tuple[float, float, float, float]
                ] = []

                try:
                    table_finder = (
                        page.find_tables()
                    )

                    tables = (
                        table_finder.tables
                    )

                except Exception:
                    # Some PDFs may not support useful
                    # table detection. Text parsing should
                    # still continue.
                    tables = []

                for table_index, table in enumerate(
                    tables
                ):
                    bbox = tuple(
                        table.bbox
                    )

                    rows = table.extract()

                    if not rows:
                        continue

                    normalized_rows = (
                        self._normalize_table_rows(
                            rows
                        )
                    )

                    if not normalized_rows:
                        continue

                    column_count = max(
                        len(row)
                        for row
                        in normalized_rows
                    )

                    row_count = len(
                        normalized_rows
                    )

                    # Ignore things that are unlikely to
                    # actually be tables.
                    if (
                        row_count < 2
                        or column_count < 2
                    ):
                        continue

                    markdown = (
                        self._table_to_markdown(
                            normalized_rows
                        )
                    )

                    if not markdown.strip():
                        continue

                    table_bboxes.append(
                        bbox
                    )

                    page_blocks.append(
                        {
                            "kind": "table",

                            "text": markdown,

                            "page_number":
                                page_index + 1,

                            "page_height":
                                page_height,

                            "bbox":
                                bbox,

                            "table_index":
                                table_index,

                            "row_count":
                                row_count,

                            "column_count":
                                column_count,

                            "font_sizes": [],

                            "bold": False,
                        }
                    )

                # ----------------------------------------------
                # Extract regular text blocks
                # ----------------------------------------------

                page_dict = page.get_text(
                    "dict"
                )

                for block in page_dict.get(
                    "blocks",
                    [],
                ):
                    # 0 = text
                    if block.get("type") != 0:
                        continue

                    block_bbox = block.get(
                        "bbox"
                    )

                    if not block_bbox:
                        continue

                    # If most of this text block is inside
                    # a detected table, skip it.
                    #
                    # The table element will preserve it.
                    if any(
                        self._bbox_overlap_ratio(
                            block_bbox,
                            table_bbox,
                        )
                        >= 0.50
                        for table_bbox
                        in table_bboxes
                    ):
                        continue

                    lines: list[str] = []

                    font_sizes: list[
                        float
                    ] = []

                    bold_detected = False

                    for line in block.get(
                        "lines",
                        [],
                    ):
                        line_parts: list[
                            str
                        ] = []

                        for span in line.get(
                            "spans",
                            [],
                        ):
                            text = (
                                span
                                .get(
                                    "text",
                                    "",
                                )
                                .strip()
                            )

                            if not text:
                                continue

                            line_parts.append(
                                text
                            )

                            size = span.get(
                                "size"
                            )

                            if size is not None:
                                font_sizes.append(
                                    float(size)
                                )

                            font_name = (
                                span
                                .get(
                                    "font",
                                    "",
                                )
                                .lower()
                            )

                            if (
                                "bold"
                                in font_name
                                or "black"
                                in font_name
                                or "semibold"
                                in font_name
                            ):
                                bold_detected = (
                                    True
                                )

                        line_text = " ".join(
                            line_parts
                        ).strip()

                        if line_text:
                            lines.append(
                                line_text
                            )

                    text = " ".join(
                        lines
                    ).strip()

                    if not text:
                        continue

                    page_blocks.append(
                        {
                            "kind": "text",

                            "text": text,

                            "page_number":
                                page_index + 1,

                            "page_height":
                                page_height,

                            "font_sizes":
                                font_sizes,

                            "bold":
                                bold_detected,

                            "bbox":
                                tuple(
                                    block_bbox
                                ),
                        }
                    )

                # ----------------------------------------------
                # Restore approximate reading order
                #
                # Sort by vertical position first,
                # then horizontal position.
                # ----------------------------------------------

                page_blocks.sort(
                    key=lambda item: (
                        (
                            item["bbox"][1]
                            if item.get(
                                "bbox"
                            )
                            else 0
                        ),
                        (
                            item["bbox"][0]
                            if item.get(
                                "bbox"
                            )
                            else 0
                        ),
                    )
                )

                raw_blocks.extend(
                    page_blocks
                )

            # ==================================================
            # PASS 2
            # Remove PDF headers / footers / page numbers
            # ==================================================

            cleaned_blocks = [
                block
                for block in raw_blocks
                if not self._is_noise(
                    block
                )
            ]

            # ==================================================
            # PASS 3
            # Estimate body font size
            #
            # Only regular text contributes.
            # Tables do not have font-size information here.
            # ==================================================

            all_font_sizes: list[
                float
            ] = []

            for block in cleaned_blocks:
                if (
                    block.get("kind")
                    != "text"
                ):
                    continue

                all_font_sizes.extend(
                    block[
                        "font_sizes"
                    ]
                )

            body_font_size = (
                median(
                    all_font_sizes
                )
                if all_font_sizes
                else 11.0
            )

            # ==================================================
            # PASS 4
            # Convert into normalized ParsedElements
            # ==================================================

            elements: list[
                ParsedElement
            ] = []

            heading_stack: dict[
                int,
                str,
            ] = {}

            for block in cleaned_blocks:

                # ==============================================
                # TABLE
                # ==============================================

                if (
                    block.get("kind")
                    == "table"
                ):
                    heading_path = [
                        heading_stack[level]
                        for level
                        in sorted(
                            heading_stack
                        )
                    ]

                    section_title = (
                        heading_path[-1]
                        if heading_path
                        else None
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "table",

                            content=
                                block["text"],

                            page_number=
                                block[
                                    "page_number"
                                ],

                            section_title=
                                section_title,

                            heading_path=
                                heading_path,

                            metadata={
                                "bbox":
                                    block[
                                        "bbox"
                                    ],

                                "table_index":
                                    block[
                                        "table_index"
                                    ],

                                "row_count":
                                    block[
                                        "row_count"
                                    ],

                                "column_count":
                                    block[
                                        "column_count"
                                    ],
                            },
                        )
                    )

                    continue

                # ==============================================
                # NORMAL TEXT
                # ==============================================

                text = block[
                    "text"
                ]

                if block[
                    "font_sizes"
                ]:
                    block_font_size = max(
                        block[
                            "font_sizes"
                        ]
                    )

                else:
                    block_font_size = (
                        body_font_size
                    )

                heading_level = (
                    self._get_heading_level(
                        text=text,

                        font_size=
                            block_font_size,

                        body_font_size=
                            body_font_size,

                        bold=
                            block[
                                "bold"
                            ],
                    )
                )

                # ==============================================
                # HEADING
                # ==============================================

                if heading_level is not None:

                    # Remove this heading level
                    # and everything below it.
                    for existing_level in list(
                        heading_stack.keys()
                    ):
                        if (
                            existing_level
                            >= heading_level
                        ):
                            del heading_stack[
                                existing_level
                            ]

                    heading_stack[
                        heading_level
                    ] = text

                    heading_path = [
                        heading_stack[level]
                        for level
                        in sorted(
                            heading_stack
                        )
                    ]

                    elements.append(
                        ParsedElement(
                            element_type=
                                "heading",

                            content=text,

                            page_number=
                                block[
                                    "page_number"
                                ],

                            section_title=
                                text,

                            heading_path=
                                heading_path,

                            metadata={
                                "heading_level":
                                    heading_level,

                                "font_size":
                                    block_font_size,

                                "bold":
                                    block[
                                        "bold"
                                    ],

                                "bbox":
                                    block[
                                        "bbox"
                                    ],
                            },
                        )
                    )

                    continue

                # ==============================================
                # PARAGRAPH
                # ==============================================

                heading_path = [
                    heading_stack[level]
                    for level
                    in sorted(
                        heading_stack
                    )
                ]

                section_title = (
                    heading_path[-1]
                    if heading_path
                    else None
                )

                elements.append(
                    ParsedElement(
                        element_type=
                            "paragraph",

                        content=text,

                        page_number=
                            block[
                                "page_number"
                            ],

                        section_title=
                            section_title,

                        heading_path=
                            heading_path,

                        metadata={
                            "font_size":
                                block_font_size,

                            "bbox":
                                block[
                                    "bbox"
                                ],
                        },
                    )
                )

            return elements

        finally:
            document.close()

    # ==========================================================
    # TABLE NORMALIZATION
    # ==========================================================

    def _normalize_table_rows(
        self,
        rows: list,
    ) -> list[list[str]]:

        normalized: list[
            list[str]
        ] = []

        for row in rows:

            normalized_row: list[
                str
            ] = []

            for cell in row:

                if cell is None:
                    value = ""

                else:
                    value = str(
                        cell
                    )

                value = (
                    value
                    .replace(
                        "\n",
                        " ",
                    )
                    .replace(
                        "\r",
                        " ",
                    )
                    .strip()
                )

                # Markdown table escaping
                value = value.replace(
                    "|",
                    "\\|",
                )

                normalized_row.append(
                    value
                )

            # Ignore completely empty rows
            if any(
                cell
                for cell
                in normalized_row
            ):
                normalized.append(
                    normalized_row
                )

        return normalized

    # ==========================================================
    # TABLE → MARKDOWN
    # ==========================================================

    def _table_to_markdown(
        self,
        rows: list[list[str]],
    ) -> str:

        if not rows:
            return ""

        column_count = max(
            len(row)
            for row
            in rows
        )

        padded_rows = []

        for row in rows:
            padded = (
                row
                + [""] * (
                    column_count
                    - len(row)
                )
            )

            padded_rows.append(
                padded
            )

        header = padded_rows[0]

        lines = [
            "| "
            + " | ".join(
                header
            )
            + " |",

            "| "
            + " | ".join(
                ["---"]
                * column_count
            )
            + " |",
        ]

        for row in padded_rows[1:]:
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
    # BOUNDING BOX OVERLAP
    # ==========================================================

    def _bbox_overlap_ratio(
        self,
        block_bbox,
        table_bbox,
    ) -> float:

        ax0, ay0, ax1, ay1 = (
            block_bbox
        )

        bx0, by0, bx1, by1 = (
            table_bbox
        )

        intersection_width = max(
            0.0,
            min(
                ax1,
                bx1,
            )
            - max(
                ax0,
                bx0,
            ),
        )

        intersection_height = max(
            0.0,
            min(
                ay1,
                by1,
            )
            - max(
                ay0,
                by0,
            ),
        )

        intersection_area = (
            intersection_width
            * intersection_height
        )

        block_area = max(
            0.0,
            ax1 - ax0,
        ) * max(
            0.0,
            ay1 - ay0,
        )

        if block_area == 0:
            return 0.0

        return (
            intersection_area
            / block_area
        )

    # ==========================================================
    # HEADING LEVEL DETECTION
    # ==========================================================

    def _get_heading_level(
        self,
        *,
        text: str,
        font_size: float,
        body_font_size: float,
        bold: bool,
    ) -> int | None:

        if len(text) > 120:
            return None

        # Major section
        if (
            font_size
            >= body_font_size
            * 1.60
        ):
            return 1

        # Subsection
        if (
            font_size
            >= body_font_size
            * 1.38
        ):
            return 2

        # Smaller bold heading
        if (
            bold
            and len(text) <= 80
        ):
            return 3

        return None

    # ==========================================================
    # HEADER / FOOTER CLEANUP
    # ==========================================================

    def _is_noise(
        self,
        block: dict,
    ) -> bool:

        # Never apply header/footer rules to
        # detected tables.
        if (
            block.get("kind")
            == "table"
        ):
            return False

        text = (
            block[
                "text"
            ]
            .strip()
        )

        bbox = block.get(
            "bbox"
        )

        if not bbox:
            return False

        _, y0, _, y1 = bbox

        page_height = block[
            "page_height"
        ]

        font_sizes = block[
            "font_sizes"
        ]

        font_size = (
            max(font_sizes)
            if font_sizes
            else 0
        )

        # Standalone page number
        if (
            PAGE_NUMBER_PATTERN
            .fullmatch(text)

            and y0
            > page_height * 0.85
        ):
            return True

        # Small page header
        if (
            y1
            < page_height * 0.07

            and font_size
            <= 9.5

            and len(text)
            <= 150
        ):
            return True

        # Small page footer
        if (
            y0
            > page_height * 0.90

            and font_size
            <= 9.5

            and len(text)
            <= 150
        ):
            return True

        return False