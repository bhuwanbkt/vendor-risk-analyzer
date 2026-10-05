import re
from statistics import median

import pymupdf

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


# Matches standalone page numbers such as:
# 1
# 10
# iii
# iv
# xii
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
            # Extract PDF text blocks and layout information
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
                    # PyMuPDF block type:
                    # 0 = text
                    # 1 = image
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

                    bbox = block.get(
                        "bbox"
                    )

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
                                bbox,
                        }
                    )

            # ==================================================
            # PASS 2
            # Remove obvious PDF noise
            #
            # Examples:
            #
            # Introduction to AWS Security AWS Whitepaper
            #
            # 3
            #
            # Infrastructure Security 4
            # ==================================================

            cleaned_blocks: list[dict] = []

            for block in raw_blocks:
                if self._is_noise(
                    block
                ):
                    continue

                cleaned_blocks.append(
                    block
                )

            # ==================================================
            # PASS 3
            # Determine approximate body font size
            #
            # We calculate this after removing headers/footers.
            # ==================================================

            all_font_sizes: list[
                float
            ] = []

            for block in cleaned_blocks:
                all_font_sizes.extend(
                    block["font_sizes"]
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
            # Convert cleaned PDF blocks into ParsedElement
            # ==================================================

            elements: list[
                ParsedElement
            ] = []

            current_heading: (
                str | None
            ) = None

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

                # ==============================================
                # Heading Detection
                #
                # Current heuristic:
                #
                # - relatively large font
                # OR
                # - bold + short text
                #
                # Later we will improve this into
                # hierarchical heading levels.
                # ==============================================

                looks_like_heading = (
                    len(text) <= 120
                    and (
                        block_font_size
                        >= body_font_size
                        * 1.25

                        or (
                            block["bold"]
                            and len(text)
                            <= 80
                        )
                    )
                )

                # ----------------------------------------------
                # Heading
                # ----------------------------------------------

                if looks_like_heading:

                    current_heading = text

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

                            heading_path=[
                                text
                            ],

                            metadata={
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

                # ----------------------------------------------
                # Paragraph
                # ----------------------------------------------

                if current_heading:
                    heading_path = [
                        current_heading
                    ]

                else:
                    heading_path = []

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
                            current_heading,

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
    # PDF NOISE DETECTION
    # ==========================================================

    def _is_noise(
        self,
        block: dict,
    ) -> bool:

        text = (
            block["text"]
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

        # ======================================================
        # RULE 1
        # Remove standalone page numbers
        #
        # Examples:
        #
        # 1
        # 2
        # 12
        # iii
        # iv
        #
        # Only remove them when they appear near bottom.
        # ======================================================

        if (
            PAGE_NUMBER_PATTERN
            .fullmatch(text)
            and y0
            > page_height * 0.85
        ):
            return True

        # ======================================================
        # RULE 2
        # Remove small page headers
        #
        # Example:
        #
        # Introduction to AWS Security AWS Whitepaper
        #
        # Typically appears in top ~7% of page.
        # ======================================================

        if (
            y1
            < page_height * 0.07

            and font_size
            <= 9.5

            and len(text)
            <= 150
        ):
            return True

        # ======================================================
        # RULE 3
        # Remove small page footers
        #
        # Examples:
        #
        # Infrastructure Security 4
        #
        # Inventory and Configuration Management 5
        #
        # Monitoring and Logging 6
        #
        # Typically appears in bottom ~10% of page.
        # ======================================================

        if (
            y0
            > page_height * 0.90

            and font_size
            <= 9.5

            and len(text)
            <= 150
        ):
            return True

        # ======================================================
        # Otherwise keep block
        # ======================================================

        return False