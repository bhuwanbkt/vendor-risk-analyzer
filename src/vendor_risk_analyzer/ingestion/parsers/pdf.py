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
            # Extract text blocks + layout information
            # ==================================================

            for page_index, page in enumerate(
                document
            ):
                page_dict = page.get_text("dict")

                page_height = float(
                    page.rect.height
                )

                for block in page_dict.get(
                    "blocks",
                    [],
                ):
                    # type 0 = text block
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

                            line_parts.append(text)

                            size = span.get("size")

                            if size:
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

                    bbox = block.get("bbox")

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
            # ==================================================

            cleaned_blocks: list[dict] = []

            for block in raw_blocks:
                if self._is_noise(block):
                    continue

                cleaned_blocks.append(
                    block
                )


            # ==================================================
            # Determine normal body font size
            #
            # Important:
            # calculate this AFTER removing headers/footers.
            # ==================================================

            all_font_sizes: list[float] = []

            for block in cleaned_blocks:
                all_font_sizes.extend(
                    block["font_sizes"]
                )

            body_font_size = (
                median(all_font_sizes)
                if all_font_sizes
                else 11.0
            )


            # ==================================================
            # PASS 3
            # Convert blocks → ParsedElement
            # ==================================================

            elements: list[
                ParsedElement
            ] = []

            current_heading: str | None = None

            for block in cleaned_blocks:
                text = block["text"]

                block_font_size = (
                    max(block["font_sizes"])
                    if block["font_sizes"]
                    else body_font_size
                )


                # ----------------------------------------------
                # Heading heuristic
                # ----------------------------------------------

                looks_like_heading = (
                    len(text) <= 120
                    and (
                        block_font_size
                        >= body_font_size * 1.25

                        or (
                            block["bold"]
                            and len(text) <= 80
                        )
                    )
                )


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


                heading_path = (
                    [current_heading]
                    if current_heading
                    else []
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
    # Noise detection
    # ==========================================================

    def _is_noise(
        self,
        block: dict,
    ) -> bool:

        text = block["text"].strip()

        bbox = block.get("bbox")

        if not bbox:
            return False

        _, y0, _, y1 = bbox

        page_height = block[
            "page_height"
        ]

        font_size = (
            max(block["font_sizes"])
            if block["font_sizes"]
            else 0
        )


        # ------------------------------------------------------
        # 1. Standalone page numbers
        #
        # Examples:
        # 2
        # 11
        # iii
        # iv
        # ------------------------------------------------------

        if (
            PAGE_NUMBER_PATTERN.fullmatch(
                text
            )
            and y0 > page_height * 0.85
        ):
            return True


        # ------------------------------------------------------
        # 2. Small text at very top of page
        #
        # Example:
        # Introduction to AWS Security AWS Whitepaper
        # ------------------------------------------------------

        if (
            y1 < 45
            and font_size <= 9.5
            and len(text) <= 150
        ):
            return True


        # ------------------------------------------------------
        # 3. Small footer text
        #
        # Examples:
        # Infrastructure Security 4
        # Data Encryption 5
        # ------------------------------------------------------

        if (
            y0 > page_height - 45
            and font_size <= 9.5
            and len(text) <= 150
        ):
            return True


        return False