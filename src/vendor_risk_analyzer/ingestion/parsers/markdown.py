import re

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


HEADING_PATTERN = re.compile(
    r"^(#{1,6})\s+(.+)$"
)


class MarkdownParser(BaseParser):
    supported_extensions = {
        "md",
        "markdown",
    }

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        text = content.decode(
            "utf-8",
            errors="replace",
        )

        elements: list[ParsedElement] = []

        heading_stack: list[str] = []
        paragraph_lines: list[str] = []

        def flush_paragraph() -> None:
            if not paragraph_lines:
                return

            paragraph = " ".join(
                line.strip()
                for line in paragraph_lines
            ).strip()

            if paragraph:
                elements.append(
                    ParsedElement(
                        element_type="paragraph",
                        content=paragraph,
                        section_title=(
                            heading_stack[-1]
                            if heading_stack
                            else None
                        ),
                        heading_path=(
                            heading_stack.copy()
                        ),
                    )
                )

            paragraph_lines.clear()

        for raw_line in text.splitlines():
            line = raw_line.strip()

            heading_match = (
                HEADING_PATTERN.match(line)
            )

            if heading_match:
                flush_paragraph()

                level = len(
                    heading_match.group(1)
                )

                title = (
                    heading_match
                    .group(2)
                    .strip()
                )

                heading_stack[:] = (
                    heading_stack[: level - 1]
                )

                heading_stack.append(title)

                elements.append(
                    ParsedElement(
                        element_type="heading",
                        content=title,
                        section_title=title,
                        heading_path=(
                            heading_stack.copy()
                        ),
                        metadata={
                            "heading_level": level,
                        },
                    )
                )

                continue

            if not line:
                flush_paragraph()
                continue

            paragraph_lines.append(line)

        flush_paragraph()

        return elements