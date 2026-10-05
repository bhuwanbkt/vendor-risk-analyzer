from statistics import median

import pymupdf

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
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
            raw_blocks = []

            # ------------------------------------------------
            # First pass:
            # collect text blocks and font information
            # ------------------------------------------------

            for page_index, page in enumerate(
                document
            ):
                page_dict = page.get_text(
                    "dict"
                )

                for block in page_dict.get(
                    "blocks",
                    [],
                ):
                    # type 0 = text
                    if block.get("type") != 0:
                        continue

                    lines = []
                    font_sizes = []
                    bold_detected = False

                    for line in block.get(
                        "lines",
                        [],
                    ):
                        line_parts = []

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

                    raw_blocks.append(
                        {
                            "text": text,
                            "page_number":
                                page_index + 1,
                            "font_sizes":
                                font_sizes,
                            "bold":
                                bold_detected,
                            "bbox":
                                block.get("bbox"),
                        }
                    )


            # ------------------------------------------------
            # Determine approximate normal body font size
            # ------------------------------------------------

            all_font_sizes = []

            for block in raw_blocks:
                all_font_sizes.extend(
                    block["font_sizes"]
                )

            body_font_size = (
                median(all_font_sizes)
                if all_font_sizes
                else 11.0
            )


            # ------------------------------------------------
            # Second pass:
            # convert PDF blocks into ParsedElement objects
            # ------------------------------------------------

            elements: list[
                ParsedElement
            ] = []

            current_heading: str | None = (
                None
            )

            for block in raw_blocks:
                text = block["text"]

                block_font_size = (
                    max(
                        block["font_sizes"]
                    )
                    if block[
                        "font_sizes"
                    ]
                    else body_font_size
                )

                # Basic heading heuristic.
                #
                # Large/short text is likely a heading.
                # Bold short text may also be a heading.

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