from __future__ import annotations

import csv
from io import StringIO

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)
from vendor_risk_analyzer.ingestion.parsers.tabular import (
    TabularRow,
    rows_to_table_elements,
)


class CSVParser(BaseParser):
    """
    Structure-aware CSV parser.

    Features:
    - UTF-8 / UTF-8 BOM / UTF-16 / cp1252 decoding
    - delimiter sniffing for comma, semicolon, tab, and pipe
    - quoted commas and embedded newlines via Python's csv parser
    - blank-row separation into multiple logical tables
    - conservative header detection
    - retrieval-safe row grouping for large CSV files
    """

    supported_extensions = {
        "csv",
    }

    parser_version = "1.1"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:
        text, encoding = self._decode_text(
            content
        )

        if not text.strip():
            return []

        dialect = self._detect_dialect(
            text
        )

        has_header = self._detect_header(
            text,
            dialect,
        )

        reader = csv.reader(
            StringIO(text),
            dialect,
        )

        rows: list[TabularRow] = []

        for row_number, row in enumerate(
            reader,
            start=1,
        ):
            rows.append(
                TabularRow(
                    row_number=row_number,
                    values=list(row),
                )
            )

        elements, _ = rows_to_table_elements(
            rows,
            source_format="csv",
            table_index_start=0,
            sheet_name=None,
            has_header=has_header,
            extra_metadata={
                "delimiter":
                    dialect.delimiter,
                "encoding":
                    encoding,
                "has_header":
                    has_header,
            },
        )

        return elements

    def _decode_text(
        self,
        content: bytes,
    ) -> tuple[str, str]:
        if content.startswith(
            (
                b"\xff\xfe",
                b"\xfe\xff",
            )
        ):
            return (
                content.decode("utf-16"),
                "utf-16",
            )

        if content.startswith(
            b"\xef\xbb\xbf"
        ):
            return (
                content.decode("utf-8-sig"),
                "utf-8-sig",
            )

        try:
            return (
                content.decode("utf-8"),
                "utf-8",
            )

        except UnicodeDecodeError:
            return (
                content.decode(
                    "cp1252",
                    errors="replace",
                ),
                "cp1252",
            )

    def _detect_dialect(
        self,
        text: str,
    ) -> csv.Dialect:
        sample = text[:16384]

        try:
            return csv.Sniffer().sniff(
                sample,
                delimiters=",;\t|",
            )

        except csv.Error:
            return csv.excel

    def _detect_header(
        self,
        text: str,
        dialect: csv.Dialect,
    ) -> bool:
        sample = text[:16384]

        try:
            return bool(
                csv.Sniffer().has_header(
                    sample
                )
            )

        except csv.Error:
            # Vendor-risk CSV exports almost always include
            # column names. Defaulting to True preserves the
            # most common schema without losing row content.
            return True
