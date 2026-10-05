import math
import re
import unicodedata
from collections import defaultdict
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

TABLE_CAPTION_PATTERN = re.compile(
    r"^table\s+[A-Za-z]?\d+(?:[.\-:])?\b",
    re.IGNORECASE,
)

FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:fig(?:ure)?\.?)\s+[A-Za-z]?\d+(?:[.\-:])?\b",
    re.IGNORECASE,
)

# Examples:
#
# ID.AM-01:
# GV.OC-01:
# o ID.AM-01:
#
# These are control/subcategory items, not section headings.
CONTROL_ITEM_PATTERN = re.compile(
    r"^(?:[oO]\s+)?"
    r"[A-Z]{2,6}\.[A-Z]{2,6}-\d{2}\s*:"
)

BULLET_PATTERN = re.compile(
    r"^[•●▪◦‣]\s*"
)


class PDFParser(BaseParser):
    supported_extensions = {"pdf"}

    parser_version = "1.5"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        document = pymupdf.open(
            stream=content,
            filetype="pdf",
        )

        try:
            # ==================================================
            # PDF BOOKMARK / OUTLINE INFORMATION
            #
            # If the PDF contains a real outline, it is stronger
            # heading evidence than font-size guessing.
            # ==================================================

            toc_levels = (
                self._build_toc_level_map(
                    document
                )
            )

            raw_blocks: list[dict] = []

            # ==================================================
            # PASS 1
            #
            # Extract:
            #
            # - normal text
            # - font information
            # - bounding boxes
            # - tables
            # - page numbers
            # ==================================================

            for page_index, page in enumerate(
                document
            ):
                page_number = (
                    page_index + 1
                )

                page_height = float(
                    page.rect.height
                )

                page_blocks: list[
                    dict
                ] = []

                table_bboxes: list[
                    tuple[
                        float,
                        float,
                        float,
                        float,
                    ]
                ] = []

                # ==============================================
                # TABLE DETECTION
                # ==============================================

                try:
                    table_finder = (
                        page.find_tables()
                    )

                    tables = (
                        table_finder.tables
                    )

                except Exception:
                    # Text parsing must still work even if one
                    # page has malformed table geometry.
                    tables = []

                for (
                    table_index,
                    table,
                ) in enumerate(
                    tables
                ):
                    try:
                        bbox = tuple(
                            float(value)
                            for value
                            in table.bbox
                        )

                        rows = (
                            table.extract()
                        )

                    except Exception:
                        # Some malformed PDF tables can expose
                        # invalid/empty geometry. Skip the table,
                        # not the whole document.
                        continue

                    if not rows:
                        continue

                    normalized_rows = (
                        self._normalize_table_rows(
                            rows
                        )
                    )

                    if not normalized_rows:
                        continue

                    row_count = len(
                        normalized_rows
                    )

                    column_count = max(
                        len(row)
                        for row
                        in normalized_rows
                    )

                    # Avoid false-positive one-cell layouts.
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
                            "kind":
                                "table",

                            "text":
                                markdown,

                            "page_number":
                                page_number,

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

                            "font_sizes":
                                [],

                            "bold":
                                False,
                        }
                    )

                # ==============================================
                # REGULAR TEXT EXTRACTION
                # ==============================================

                page_dict = (
                    page.get_text(
                        "dict"
                    )
                )

                for block in page_dict.get(
                    "blocks",
                    [],
                ):

                    # PyMuPDF:
                    #
                    # 0 = text
                    # 1 = image
                    #
                    if block.get("type") != 0:
                        continue

                    block_bbox = (
                        block.get(
                            "bbox"
                        )
                    )

                    if not block_bbox:
                        continue

                    block_bbox = tuple(
                        float(value)
                        for value
                        in block_bbox
                    )

                    # ------------------------------------------
                    # TABLE TEXT DEDUPLICATION
                    #
                    # Text inside a detected table should not
                    # also become ordinary paragraph content.
                    # ------------------------------------------

                    inside_table = any(
                        self._bbox_overlap_ratio(
                            block_bbox,
                            table_bbox,
                        )
                        >= 0.50

                        for table_bbox
                        in table_bboxes
                    )

                    if inside_table:
                        continue

                    lines: list[
                        str
                    ] = []

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

                            span_text = (
                                self
                                ._normalize_inline_text(
                                    span.get(
                                        "text",
                                        "",
                                    )
                                )
                            )

                            if not span_text:
                                continue

                            line_parts.append(
                                span_text
                            )

                            size = span.get(
                                "size"
                            )

                            if (
                                size
                                is not None
                            ):
                                font_sizes.append(
                                    float(
                                        size
                                    )
                                )

                            font_name = (
                                span
                                .get(
                                    "font",
                                    "",
                                )
                                .lower()
                            )

                            if any(
                                weight
                                in font_name

                                for weight in (
                                    "bold",
                                    "black",
                                    "semibold",
                                    "demi",
                                )
                            ):
                                bold_detected = (
                                    True
                                )

                        line_text = (
                            " ".join(
                                line_parts
                            )
                            .strip()
                        )

                        if line_text:
                            lines.append(
                                line_text
                            )

                    text = (
                        " ".join(
                            lines
                        )
                        .strip()
                    )

                    if not text:
                        continue

                    page_blocks.append(
                        {
                            "kind":
                                "text",

                            "text":
                                text,

                            "page_number":
                                page_number,

                            "page_height":
                                page_height,

                            "font_sizes":
                                font_sizes,

                            "bold":
                                bold_detected,

                            "bbox":
                                block_bbox,
                        }
                    )

                # ==============================================
                # APPROXIMATE READING ORDER
                #
                # Top-to-bottom, then left-to-right.
                # ==============================================

                page_blocks.sort(
                    key=lambda item: (
                        (
                            item[
                                "bbox"
                            ][1]
                            if item.get(
                                "bbox"
                            )
                            else 0
                        ),
                        (
                            item[
                                "bbox"
                            ][0]
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
            #
            # Estimate normal body font.
            # ==================================================

            body_font_size = (
                self._estimate_body_font_size(
                    raw_blocks
                )
            )

            # ==================================================
            # PASS 3
            #
            # Learn repeated headers / footers from the document.
            #
            # This fixes PDFs such as the NIST document where
            # the running header sits slightly below our older
            # fixed 7% cutoff.
            # ==================================================

            repeated_margin_signatures = (
                self
                ._find_repeated_margin_signatures(
                    raw_blocks,
                    page_count=len(
                        document
                    ),
                )
            )

            # ==================================================
            # PASS 4
            #
            # Remove:
            #
            # - repeated headers
            # - repeated footers
            # - standalone page numbers
            # - conservative small-margin noise
            # ==================================================

            cleaned_blocks = [
                block
                for block
                in raw_blocks

                if not self._is_noise(
                    block,
                    repeated_margin_signatures=(
                        repeated_margin_signatures
                    ),
                )
            ]

            # ==================================================
            # PASS 5
            #
            # Convert to ParsedElements.
            # ==================================================

            elements: list[
                ParsedElement
            ] = []

            heading_stack: dict[
                int,
                str,
            ] = {}

            # A table caption usually appears immediately before
            # a table.
            pending_table_caption: (
                str | None
            ) = None

            pending_table_caption_page: (
                int | None
            ) = None

            # Used for multi-page table continuation.
            last_table_caption: (
                str | None
            ) = None

            last_table_page: (
                int | None
            ) = None

            for block in cleaned_blocks:

                # ==============================================
                # TABLE
                # ==============================================

                if (
                    block.get(
                        "kind"
                    )
                    == "table"
                ):

                    heading_path = [
                        heading_stack[
                            level
                        ]

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

                    caption: (
                        str | None
                    ) = None

                    is_continuation = (
                        False
                    )

                    # ------------------------------------------
                    # Same-page caption:
                    #
                    # Table 1. ...
                    # [actual table]
                    # ------------------------------------------

                    if (
                        pending_table_caption
                        and
                        pending_table_caption_page
                        ==
                        block[
                            "page_number"
                        ]
                    ):

                        caption = (
                            pending_table_caption
                        )

                    # ------------------------------------------
                    # Multi-page continuation:
                    #
                    # Page 29 = Table 2 begins
                    # Page 30 = table continues near page top
                    # ------------------------------------------

                    elif (
                        last_table_caption
                        and
                        last_table_page
                        is not None

                        and
                        block[
                            "page_number"
                        ]
                        ==
                        last_table_page
                        + 1

                        and
                        block[
                            "bbox"
                        ][1]
                        <
                        block[
                            "page_height"
                        ]
                        * 0.20
                    ):

                        caption = (
                            last_table_caption
                        )

                        is_continuation = (
                            True
                        )

                    metadata = {
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

                        "is_continuation":
                            is_continuation,
                    }

                    if caption:
                        metadata[
                            "caption"
                        ] = caption

                    elements.append(
                        ParsedElement(
                            element_type=
                                "table",

                            content=
                                block[
                                    "text"
                                ],

                            page_number=
                                block[
                                    "page_number"
                                ],

                            section_title=
                                section_title,

                            heading_path=
                                heading_path,

                            metadata=
                                metadata,
                        )
                    )

                    last_table_caption = (
                        caption
                    )

                    last_table_page = (
                        block[
                            "page_number"
                        ]
                    )

                    pending_table_caption = (
                        None
                    )

                    pending_table_caption_page = (
                        None
                    )

                    continue

                # ==============================================
                # NORMAL TEXT BLOCK
                # ==============================================

                text = block[
                    "text"
                ]

                block_font_size = (
                    max(
                        block[
                            "font_sizes"
                        ]
                    )

                    if block[
                        "font_sizes"
                    ]

                    else body_font_size
                )

                # ==============================================
                # FIGURE / TABLE CAPTION
                #
                # Captions are NOT headings.
                # ==============================================

                caption_type = (
                    self._get_caption_type(
                        text
                    )
                )

                if (
                    caption_type
                    is not None
                ):

                    heading_path = [
                        heading_stack[
                            level
                        ]

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
                                "caption",

                            content=
                                text,

                            page_number=
                                block[
                                    "page_number"
                                ],

                            section_title=
                                section_title,

                            heading_path=
                                heading_path,

                            metadata={
                                "caption_type":
                                    caption_type,

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

                    if (
                        caption_type
                        == "table"
                    ):

                        pending_table_caption = (
                            text
                        )

                        pending_table_caption_page = (
                            block[
                                "page_number"
                            ]
                        )

                    else:

                        pending_table_caption = (
                            None
                        )

                        pending_table_caption_page = (
                            None
                        )

                    continue

                # A table caption should normally be adjacent to
                # the table. If unrelated text appears first,
                # stop carrying the pending caption.
                if (
                    pending_table_caption

                    and
                    pending_table_caption_page
                    ==
                    block[
                        "page_number"
                    ]
                ):

                    pending_table_caption = (
                        None
                    )

                    pending_table_caption_page = (
                        None
                    )

                # ==============================================
                # HEADING DETECTION
                # ==============================================

                heading_level = (
                    self._get_heading_level(
                        text=text,

                        page_number=
                            block[
                                "page_number"
                            ],

                        font_size=
                            block_font_size,

                        body_font_size=
                            body_font_size,

                        bold=
                            block[
                                "bold"
                            ],

                        toc_levels=
                            toc_levels,
                    )
                )

                if (
                    heading_level
                    is not None
                ):

                    for (
                        existing_level
                    ) in list(
                        heading_stack.keys()
                    ):

                        if (
                            existing_level
                            >=
                            heading_level
                        ):

                            del heading_stack[
                                existing_level
                            ]

                    heading_stack[
                        heading_level
                    ] = text

                    heading_path = [
                        heading_stack[
                            level
                        ]

                        for level
                        in sorted(
                            heading_stack
                        )
                    ]

                    # A real heading ends any previous table
                    # continuation context.
                    last_table_caption = (
                        None
                    )

                    last_table_page = (
                        None
                    )

                    elements.append(
                        ParsedElement(
                            element_type=
                                "heading",

                            content=
                                text,

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
                    heading_stack[
                        level
                    ]

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

                        content=
                            text,

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
    # PDF OUTLINE / BOOKMARK HEADINGS
    # ==========================================================

    def _build_toc_level_map(
        self,
        document: pymupdf.Document,
    ) -> dict[
        tuple[int, str],
        int,
    ]:

        result: dict[
            tuple[int, str],
            int,
        ] = {}

        try:
            toc = document.get_toc()

        except Exception:
            return result

        for item in toc:

            if len(item) < 3:
                continue

            level, title, page_number = (
                item[:3]
            )

            if (
                not isinstance(
                    page_number,
                    int,
                )
                or page_number <= 0
            ):
                continue

            title_key = (
                self._normalize_match_text(
                    str(title)
                )
            )

            if not title_key:
                continue

            level = max(
                1,
                min(
                    int(level),
                    3,
                ),
            )

            key = (
                page_number,
                title_key,
            )

            result[key] = min(
                result.get(
                    key,
                    level,
                ),
                level,
            )

        return result

    # ==========================================================
    # BODY FONT ESTIMATION
    # ==========================================================

    def _estimate_body_font_size(
        self,
        blocks: list[dict],
    ) -> float:

        central_sizes: list[
            float
        ] = []

        fallback_sizes: list[
            float
        ] = []

        for block in blocks:

            if (
                block.get(
                    "kind"
                )
                != "text"
            ):
                continue

            sizes = [
                float(size)

                for size
                in block.get(
                    "font_sizes",
                    [],
                )

                if (
                    size
                    and float(
                        size
                    ) > 0
                )
            ]

            if not sizes:
                continue

            block_size = float(
                median(
                    sizes
                )
            )

            fallback_sizes.append(
                block_size
            )

            bbox = block.get(
                "bbox"
            )

            page_height = (
                block.get(
                    "page_height"
                )
            )

            if (
                not bbox
                or not page_height
            ):
                continue

            _, y0, _, y1 = bbox

            # Prefer central body paragraphs and avoid page
            # margins/title areas when estimating body font.
            if (
                y0
                >=
                page_height
                * 0.12

                and
                y1
                <=
                page_height
                * 0.88

                and
                len(
                    block.get(
                        "text",
                        "",
                    )
                )
                >= 20
            ):

                central_sizes.append(
                    block_size
                )

        if central_sizes:
            return float(
                median(
                    central_sizes
                )
            )

        if fallback_sizes:
            return float(
                median(
                    fallback_sizes
                )
            )

        return 11.0

    # ==========================================================
    # REPEATED HEADER / FOOTER DETECTION
    # ==========================================================

    def _find_repeated_margin_signatures(
        self,
        blocks: list[dict],
        *,
        page_count: int,
    ) -> set[
        tuple[str, str]
    ]:

        occurrences: dict[
            tuple[str, str],
            set[int],
        ] = defaultdict(
            set
        )

        for block in blocks:

            if (
                block.get(
                    "kind"
                )
                != "text"
            ):
                continue

            zone = (
                self._get_margin_zone(
                    block
                )
            )

            if zone is None:
                continue

            signature = (
                self
                ._normalize_margin_signature(
                    block.get(
                        "text",
                        "",
                    )
                )
            )

            if len(signature) < 3:
                continue

            occurrences[
                (
                    zone,
                    signature,
                )
            ].add(
                int(
                    block[
                        "page_number"
                    ]
                )
            )

        # Require at least three different pages.
        #
        # For large PDFs, 5% of the document is enough to
        # identify a repeated running header/footer.
        minimum_pages = max(
            3,
            math.ceil(
                max(
                    page_count,
                    1,
                )
                * 0.05
            ),
        )

        return {
            key

            for (
                key,
                pages,
            )
            in occurrences.items()

            if len(
                pages
            )
            >= minimum_pages
        }

    def _get_margin_zone(
        self,
        block: dict,
    ) -> str | None:

        bbox = block.get(
            "bbox"
        )

        page_height = (
            block.get(
                "page_height"
            )
        )

        if (
            not bbox
            or not page_height
        ):
            return None

        _, y0, _, y1 = bbox

        if (
            y1
            <=
            page_height
            * 0.12
        ):
            return "header"

        if (
            y0
            >=
            page_height
            * 0.88
        ):
            return "footer"

        return None

    def _normalize_margin_signature(
        self,
        text: str,
    ) -> str:

        value = (
            self
            ._normalize_inline_text(
                text
            )
            .casefold()
        )

        # Page 3 of 25
        value = re.sub(
            r"\bpage\s+\d+"
            r"\s*(?:of\s+\d+)?\b",

            "page #",

            value,
        )

        # Infrastructure Security 4
        #
        # Appendix iv
        value = re.sub(
            r"\s+"
            r"(?:\d+|[ivxlcdm]+)"
            r"\s*$",

            " #",

            value,

            flags=
                re.IGNORECASE,
        )

        return value.strip()

    # ==========================================================
    # HEADER / FOOTER / PAGE NUMBER FILTERING
    # ==========================================================

    def _is_noise(
        self,
        block: dict,
        *,
        repeated_margin_signatures: set[
            tuple[str, str]
        ],
    ) -> bool:

        # Never remove tables with margin rules.
        if (
            block.get(
                "kind"
            )
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

        font_sizes = block.get(
            "font_sizes",
            [],
        )

        font_size = (
            max(
                font_sizes
            )
            if font_sizes
            else 0.0
        )

        # ------------------------------------------
        # Standalone page number
        # ------------------------------------------

        if (
            PAGE_NUMBER_PATTERN
            .fullmatch(
                text
            )

            and
            y0
            >
            page_height
            * 0.82
        ):
            return True

        # ------------------------------------------
        # Repeated running header / footer
        # ------------------------------------------

        zone = (
            self._get_margin_zone(
                block
            )
        )

        if zone is not None:

            signature = (
                self
                ._normalize_margin_signature(
                    text
                )
            )

            if (
                (
                    zone,
                    signature,
                )
                in
                repeated_margin_signatures
            ):
                return True

        # ------------------------------------------
        # Very conservative small top-margin noise.
        #
        # Repetition detection above handles most
        # real running headers.
        # ------------------------------------------

        if (
            y1
            <
            page_height
            * 0.045

            and
            font_size
            <= 9.0

            and
            len(text)
            <= 180
        ):
            return True

        # ------------------------------------------
        # Small footer text.
        #
        # Preserves body footnotes with normal
        # 10-12pt text while removing tiny running
        # footer labels.
        # ------------------------------------------

        if (
            y0
            >
            page_height
            * 0.90

            and
            font_size
            <= 9.5

            and
            len(text)
            <= 180
        ):
            return True

        return False

    # ==========================================================
    # CAPTION DETECTION
    # ==========================================================

    def _get_caption_type(
        self,
        text: str,
    ) -> str | None:

        text = (
            self
            ._normalize_inline_text(
                text
            )
        )

        if (
            TABLE_CAPTION_PATTERN
            .match(
                text
            )
        ):
            return "table"

        if (
            FIGURE_CAPTION_PATTERN
            .match(
                text
            )
        ):
            return "figure"

        return None

    # ==========================================================
    # CONTROL / LIST DETECTION
    # ==========================================================

    def _looks_like_control_or_list(
        self,
        text: str,
    ) -> bool:

        text = (
            self
            ._normalize_inline_text(
                text
            )
        )

        return bool(
            BULLET_PATTERN
            .match(
                text
            )

            or

            CONTROL_ITEM_PATTERN
            .match(
                text
            )
        )

    # ==========================================================
    # HEADING DETECTION
    # ==========================================================

    def _get_heading_level(
        self,
        *,
        text: str,
        page_number: int,
        font_size: float,
        body_font_size: float,
        bold: bool,
        toc_levels: dict[
            tuple[int, str],
            int,
        ],
    ) -> int | None:

        text = (
            self
            ._normalize_inline_text(
                text
            )
        )

        if (
            not text
            or len(text) > 120
        ):
            return None

        # Figure and table captions are structure, but they are
        # not document headings.
        if (
            self._get_caption_type(
                text
            )
            is not None
        ):
            return None

        # NIST controls / bullet lines should never alter the
        # heading stack.
        if (
            self
            ._looks_like_control_or_list(
                text
            )
        ):
            return None

        # ------------------------------------------
        # Strongest signal:
        # real PDF outline/bookmark
        # ------------------------------------------

        toc_level = (
            toc_levels.get(
                (
                    page_number,
                    self
                    ._normalize_match_text(
                        text
                    ),
                )
            )
        )

        if (
            toc_level
            is not None
        ):
            return toc_level

        # Long full sentences are poor heading candidates.
        if (
            len(text) > 55
            and
            text.endswith(
                (
                    ".",
                    ";",
                )
            )
        ):
            return None

        # ------------------------------------------
        # Level 1
        # ------------------------------------------

        if (
            font_size
            >=
            body_font_size
            * 1.60
        ):
            return 1

        # ------------------------------------------
        # Level 2
        # ------------------------------------------

        if (
            font_size
            >=
            body_font_size
            * 1.35
        ):
            return 2

        if not bold:
            return None

        word_count = len(
            text.split()
        )

        numbered_heading = bool(
            re.match(
                r"^\d+"
                r"(?:\.\d+)*"
                r"\.?\s+\S",

                text,
            )
        )

        # ------------------------------------------
        # Level 3:
        # numbered section
        #
        # 1. Overview
        # 2.1 Risk Management
        # ------------------------------------------

        if (
            numbered_heading

            and
            font_size
            >=
            body_font_size
            * 0.95

            and
            len(text)
            <= 100
        ):
            return 3

        # ------------------------------------------
        # Level 3:
        # clearly larger bold label
        # ------------------------------------------

        if (
            font_size
            >=
            body_font_size
            * 1.08

            and
            len(text)
            <= 80

            and
            word_count
            <= 12
        ):
            return 3

        # ------------------------------------------
        # Level 3:
        # very short bold body-size label
        #
        # Topics
        # Preface
        # Acknowledgments
        # Note to Readers
        #
        # But NOT long controls / sentences.
        # ------------------------------------------

        if (
            font_size
            >=
            body_font_size
            * 0.95

            and
            len(text)
            <= 35

            and
            word_count
            <= 6

            and
            ":"
            not in text
        ):
            return 3

        return None

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

                value = (
                    ""
                    if cell is None
                    else str(
                        cell
                    )
                )

                value = (
                    self
                    ._normalize_inline_text(
                        value
                    )
                )

                # Markdown escaping.
                value = value.replace(
                    "|",
                    "\\|",
                )

                normalized_row.append(
                    value
                )

            if any(
                cell.strip()
                for cell
                in normalized_row
            ):
                normalized.append(
                    normalized_row
                )

        if not normalized:
            return []

        max_columns = max(
            len(row)
            for row
            in normalized
        )

        padded_rows = [
            row
            + [""] * (
                max_columns
                - len(row)
            )

            for row
            in normalized
        ]

        # Remove structurally empty columns.
        #
        # This is what fixed the NIST 9-column table
        # into its real 3-column representation.
        columns_to_keep = [
            column_index

            for column_index
            in range(
                max_columns
            )

            if any(
                row[
                    column_index
                ].strip()

                for row
                in padded_rows
            )
        ]

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
    # TABLE -> MARKDOWN
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
            for row
            in rows
        )

        if column_count == 0:
            return ""

        padded_rows = [
            row
            + [""] * (
                column_count
                - len(row)
            )

            for row
            in rows
        ]

        lines = [
            (
                "| "
                + " | ".join(
                    padded_rows[0]
                )
                + " |"
            ),
            (
                "| "
                + " | ".join(
                    ["---"]
                    * column_count
                )
                + " |"
            ),
        ]

        for row in padded_rows[
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
            -
            max(
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
            -
            max(
                ay0,
                by0,
            ),
        )

        intersection_area = (
            intersection_width
            *
            intersection_height
        )

        block_area = (
            max(
                0.0,
                ax1 - ax0,
            )
            *
            max(
                0.0,
                ay1 - ay0,
            )
        )

        if block_area == 0:
            return 0.0

        return (
            intersection_area
            /
            block_area
        )

    # ==========================================================
    # TEXT NORMALIZATION
    # ==========================================================

    def _normalize_inline_text(
        self,
        value: str,
    ) -> str:

        value = (
            unicodedata.normalize(
                "NFKC",
                str(
                    value
                ),
            )
        )

        # Remove invisible Unicode format characters.
        value = "".join(
            char

            for char
            in value

            if (
                unicodedata.category(
                    char
                )
                != "Cf"
            )
        )

        value = (
            value
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

    def _normalize_match_text(
        self,
        value: str,
    ) -> str:

        return (
            self
            ._normalize_inline_text(
                value
            )
            .casefold()
        )