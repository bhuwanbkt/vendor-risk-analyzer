from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)


class TextParser(BaseParser):
    supported_extensions = {"txt"}

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:

        text = content.decode(
            "utf-8",
            errors="replace",
        )

        blocks = [
            block.strip()
            for block in text.split("\n\n")
            if block.strip()
        ]

        elements: list[ParsedElement] = []

        for block in blocks:
            elements.append(
                ParsedElement(
                    element_type="paragraph",
                    content=block,
                )
            )

        return elements