from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
)
from vendor_risk_analyzer.ingestion.parsers.markdown import (
    MarkdownParser,
)
from vendor_risk_analyzer.ingestion.parsers.text import (
    TextParser,
)
from vendor_risk_analyzer.ingestion.parsers.pdf import (
    PDFParser,
)


PARSERS: list[BaseParser] = [
    TextParser(),
    MarkdownParser(),
    PDFParser(),
]


def get_parser(
    extension: str,
) -> BaseParser:

    extension = (
        extension
        .lower()
        .lstrip(".")
    )

    for parser in PARSERS:
        if (
            extension
            in parser.supported_extensions
        ):
            return parser

    raise ValueError(
        f"No parser available for .{extension}"
    )