from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from vendor_risk_analyzer.ingestion.parsers.base import (
    BaseParser,
    ParsedElement,
)
from vendor_risk_analyzer.ingestion.parsers.tabular import (
    TabularRow,
    rows_to_table_elements,
)


MAX_WORKSHEET_ROWS = 50_000
MAX_WORKSHEET_COLUMNS = 200


class XLSXParser(BaseParser):
    """
    Memory-conscious .xlsx parser for vendor-risk workbooks.

    Behavior:
    - uses openpyxl read-only mode
    - preserves worksheet names
    - keeps formulas as formula text rather than evaluating them
    - blank rows separate logical tables
    - title rows are detected conservatively
    - large tables are split into row groups with repeated headers
    - hidden sheets are still preserved, with sheet state metadata

    Limitations for v1.0:
    - Excel formatting is not treated as semantic structure
    - charts/images are not extracted
    - formulas are not recalculated
    - side-by-side tables without a blank row are treated as one
      wider table
    """

    supported_extensions = {
        "xlsx",
    }

    parser_version = "1.1"

    def parse(
        self,
        content: bytes,
    ) -> list[ParsedElement]:
        workbook = load_workbook(
            filename=BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )

        elements: list[ParsedElement] = []
        table_index = 0

        try:
            for worksheet in workbook.worksheets:
                if (
                    worksheet.max_row
                    and
                    worksheet.max_row
                    > MAX_WORKSHEET_ROWS
                ):
                    raise ValueError(
                        "Worksheet "
                        f"{worksheet.title!r} exceeds "
                        f"the {MAX_WORKSHEET_ROWS:,}-row "
                        "ingestion safety limit."
                    )

                if (
                    worksheet.max_column
                    and
                    worksheet.max_column
                    > MAX_WORKSHEET_COLUMNS
                ):
                    raise ValueError(
                        "Worksheet "
                        f"{worksheet.title!r} exceeds "
                        f"the {MAX_WORKSHEET_COLUMNS}-column "
                        "ingestion safety limit."
                    )

                current_block: list[
                    TabularRow
                ] = []

                for row_number, values in enumerate(
                    worksheet.iter_rows(
                        values_only=True
                    ),
                    start=1,
                ):
                    row = TabularRow(
                        row_number=row_number,
                        values=list(values),
                    )

                    if self._row_is_blank(
                        row.values
                    ):
                        if current_block:
                            (
                                block_elements,
                                table_index,
                            ) = rows_to_table_elements(
                                current_block,
                                source_format="xlsx",
                                table_index_start=
                                    table_index,
                                sheet_name=
                                    worksheet.title,
                                has_header=True,
                                extra_metadata={
                                    "sheet_state":
                                        worksheet.sheet_state,
                                    "has_header":
                                        True,
                                },
                            )

                            elements.extend(
                                block_elements
                            )

                            current_block = []

                        continue

                    current_block.append(
                        row
                    )

                if current_block:
                    (
                        block_elements,
                        table_index,
                    ) = rows_to_table_elements(
                        current_block,
                        source_format="xlsx",
                        table_index_start=
                            table_index,
                        sheet_name=
                            worksheet.title,
                        has_header=True,
                        extra_metadata={
                            "sheet_state":
                                worksheet.sheet_state,
                            "has_header":
                                True,
                        },
                    )

                    elements.extend(
                        block_elements
                    )

        finally:
            workbook.close()

        return elements

    def _row_is_blank(
        self,
        values: list[object],
    ) -> bool:
        for value in values:
            if value is None:
                continue

            if isinstance(value, str):
                if value.strip():
                    return False
                continue

            return False

        return True
