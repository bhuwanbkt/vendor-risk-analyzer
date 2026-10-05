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
            # Extract text and layout information
            # ==================================================

            for page_index, page in enumerate(
                document
            ):
                page_dict = page.get_text(
                    "dict"
                )

                page_height = float(
                    page.rect.height
                )

                for block in page_dict.get(
                    "blocks",
                    [],
                ):

                    # 0 = text block
                    if block.get("type") != 0:
                        continue

                    lines: list[str] = []
                    font_sizes: list[float] = []

                    bold_detected = False

                    for line in block.get(
                        "lines",
                        [],
                    ):

                        line_parts: list[str] = []

                        for span in line.get(
                            "spans",
                            [],
                        ):
                            text = (
                                span
                                .get("text", "")
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
                                .get("font", "")
                                .lower()
                            )

                            if (
                                "bold" in font_name
                                or "black" in font_name
                                or "semibold" in font_name
                            ):
                                bold_detected = True

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

                    raw_blocks.append(
                        {
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
                                block.get(
                                    "bbox"
                                ),
                        }
                    )

            # ==================================================
            # PASS 2
            # Remove headers, footers and page numbers
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
            # ==================================================

            all_font_sizes: list[float] = []

            for block in cleaned_blocks:
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
            # Create elements with heading hierarchy
            # ==================================================

            elements: list[
                ParsedElement
            ] = []

            # Example:
            #
            # {
            #     1: "Security Products and Features",
            #     2: "Data Encryption"
            # }
            #
            heading_stack: dict[
                int,
                str,
            ] = {}

            for block in cleaned_blocks:

                text = block[
                    "text"
                ]

                if block["font_sizes"]:
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
                            block["bold"],
                    )
                )

                # ==============================================
                # HEADING
                # ==============================================

                if heading_level is not None:

                    # Remove current heading and all
                    # deeper headings.
                    #
                    # Example:
                    #
                    # level 1: Security Products
                    # level 2: Data Encryption
                    #
                    # New level 2:
                    # Identity and Access Control
                    #
                    # Data Encryption gets replaced.

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
                        for level in sorted(
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
                    for level in sorted(
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

        # ------------------------------------------------------
        # LEVEL 1
        #
        # Major sections.
        #
        # AWS PDF example:
        #
        # Security Products and Features
        # Security Guidance
        # Compliance
        #
        # Body ≈ 12pt
        # Major heading ≈ 20pt
        # ------------------------------------------------------

        if (
            font_size
            >= body_font_size * 1.60
        ):
            return 1

        # ------------------------------------------------------
        # LEVEL 2
        #
        # Subsections.
        #
        # Example:
        #
        # Infrastructure Security
        # Data Encryption
        # Identity and Access Control
        #
        # ≈ 18pt
        # ------------------------------------------------------

        if (
            font_size
            >= body_font_size * 1.38
        ):
            return 2

        # ------------------------------------------------------
        # LEVEL 3
        #
        # Smaller bold labels/headings.
        #
        # Example:
        #
        # Topics
        # ------------------------------------------------------

        if (
            bold
            and len(text) <= 80
        ):
            return 3

        return None

    # ==========================================================
    # HEADER / FOOTER / PAGE NUMBER FILTERING
    # ==========================================================

    def _is_noise(
        self,
        block: dict,
    ) -> bool:

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

        # ------------------------------------------------------
        # Standalone page number
        #
        # 1
        # 7
        # iii
        # iv
        # ------------------------------------------------------

        if (
            PAGE_NUMBER_PATTERN
            .fullmatch(text)

            and y0
            > page_height * 0.85
        ):
            return True

        # ------------------------------------------------------
        # Small page header
        # ------------------------------------------------------

        if (
            y1
            < page_height * 0.07

            and font_size
            <= 9.5

            and len(text)
            <= 150
        ):
            return True

        # ------------------------------------------------------
        # Small page footer
        # ------------------------------------------------------

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