from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ParsedElement:
    element_type: str
    content: str
    page_number: int | None = None
    sheet_name: str | None = None
    section_title: str | None = None
    heading_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseParser:
    supported_extensions: set[str] = set()

    # Each parser can override this independently.
    parser_version: str = "1.0"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:
        raise NotImplementedError