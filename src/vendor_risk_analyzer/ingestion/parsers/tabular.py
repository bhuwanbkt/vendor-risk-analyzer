from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import math
from typing import Any

from vendor_risk_analyzer.ingestion.parsers.base import ParsedElement


MAX_DATA_ROWS_PER_ELEMENT = 20
MAX_TABLE_CHARS = 3200


@dataclass(slots=True)
class TabularRow:
    row_number: int
    values: list[Any]


def rows_to_table_elements(
    rows: list[TabularRow],
    *,
    source_format: str,
    table_index_start: int = 0,
    sheet_name: str | None = None,
    has_header: bool = True,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[list[ParsedElement], int]:
    """
    Convert tabular rows into retrieval-safe table elements.

    Important behavior:
    - blank rows separate logical tables
    - globally empty columns are removed
    - an optional one-cell title row is recognized conservatively
    - large tables are split into row groups
    - the header row is repeated in every group
    - source row ranges are retained in metadata
    """

    elements: list[ParsedElement] = []
    table_index = table_index_start
    metadata_base = dict(extra_metadata or {})

    for block in _split_blocks(rows):
        prepared = _prepare_block(
            block,
            has_header=has_header,
        )

        if prepared is None:
            continue

        (
            title,
            header,
            header_row_number,
            data_rows,
            column_count,
        ) = prepared

        groups = _group_rows(
            header,
            data_rows,
        )

        part_count = len(groups)
        total_data_rows = len(data_rows)

        heading_path: list[str] = []

        if sheet_name:
            heading_path.append(sheet_name)

        if title and (
            not heading_path
            or heading_path[-1] != title
        ):
            heading_path.append(title)

        section_title = (
            title
            or sheet_name
            or None
        )

        for part_index, group in enumerate(
            groups,
            start=1,
        ):
            table_rows = [header] + [
                row.values
                for row in group
            ]

            content = _table_to_markdown(
                table_rows
            )

            if not content:
                continue

            if group:
                source_row_start = (
                    group[0].row_number
                )
                source_row_end = (
                    group[-1].row_number
                )
            else:
                source_row_start = (
                    header_row_number
                )
                source_row_end = (
                    header_row_number
                )

            metadata: dict[str, Any] = {
                **metadata_base,
                "source_format":
                    source_format,
                "table_index":
                    table_index,
                "table_part":
                    part_index,
                "table_part_count":
                    part_count,
                "row_count":
                    1 + len(group),
                "data_row_count":
                    len(group),
                "total_data_rows":
                    total_data_rows,
                "column_count":
                    column_count,
                "column_names":
                    header.copy(),
                "header_row_number":
                    header_row_number,
                "source_row_start":
                    source_row_start,
                "source_row_end":
                    source_row_end,
                "is_continuation":
                    part_index > 1,
            }

            if sheet_name:
                metadata[
                    "sheet_name"
                ] = sheet_name

            if title:
                metadata[
                    "table_title"
                ] = title
                # Reuse the existing table-caption behavior in
                # the shared chunker so spreadsheet table titles
                # become part of retrieval content as well as
                # metadata.
                metadata[
                    "caption"
                ] = title

            elements.append(
                ParsedElement(
                    element_type="table",
                    content=content,
                    page_number=None,
                    sheet_name=sheet_name,
                    section_title=section_title,
                    heading_path=
                        heading_path.copy(),
                    metadata=metadata,
                )
            )

        table_index += 1

    return elements, table_index


def _split_blocks(
    rows: list[TabularRow],
) -> list[list[TabularRow]]:
    blocks: list[list[TabularRow]] = []
    current: list[TabularRow] = []

    for row in rows:
        if _row_is_blank(row.values):
            if current:
                blocks.append(current)
                current = []
            continue

        current.append(row)

    if current:
        blocks.append(current)

    return blocks


def _prepare_block(
    block: list[TabularRow],
    *,
    has_header: bool,
) -> tuple[
    str | None,
    list[str],
    int,
    list[TabularRow],
    int,
] | None:
    if not block:
        return None

    normalized_rows = [
        TabularRow(
            row_number=row.row_number,
            values=[
                normalize_cell_value(value)
                for value in row.values
            ],
        )
        for row in block
    ]

    normalized_rows = _remove_empty_columns(
        normalized_rows
    )

    if not normalized_rows:
        return None

    title: str | None = None

    if _looks_like_title_row(
        normalized_rows
    ):
        title = next(
            value
            for value in normalized_rows[0].values
            if value
        )

        normalized_rows = (
            normalized_rows[1:]
        )

    if not normalized_rows:
        return None

    width = max(
        len(row.values)
        for row in normalized_rows
    )

    normalized_rows = [
        TabularRow(
            row_number=row.row_number,
            values=row.values
            + [""] * (
                width
                - len(row.values)
            ),
        )
        for row in normalized_rows
    ]

    if has_header:
        header_source = (
            normalized_rows[0]
        )

        header = _normalize_headers(
            header_source.values
        )

        header_row_number = (
            header_source.row_number
        )

        data_rows = normalized_rows[1:]

    else:
        header = [
            f"Column {index}"
            for index in range(
                1,
                width + 1,
            )
        ]

        header_row_number = (
            normalized_rows[0]
            .row_number
        )

        data_rows = normalized_rows

    return (
        title,
        header,
        header_row_number,
        data_rows,
        width,
    )


def _looks_like_title_row(
    rows: list[TabularRow],
) -> bool:
    if len(rows) < 2:
        return False

    first_count = sum(
        bool(value)
        for value in rows[0].values
    )

    second_count = sum(
        bool(value)
        for value in rows[1].values
    )

    if first_count != 1:
        return False

    if second_count < 2:
        return False

    title = next(
        (
            value
            for value in rows[0].values
            if value
        ),
        "",
    )

    if len(title) > 160:
        return False

    return True


def _remove_empty_columns(
    rows: list[TabularRow],
) -> list[TabularRow]:
    if not rows:
        return []

    width = max(
        len(row.values)
        for row in rows
    )

    padded = [
        TabularRow(
            row_number=row.row_number,
            values=row.values
            + [""] * (
                width
                - len(row.values)
            ),
        )
        for row in rows
    ]

    columns_to_keep = [
        column_index
        for column_index in range(width)
        if any(
            row.values[
                column_index
            ]
            for row in padded
        )
    ]

    if not columns_to_keep:
        return []

    return [
        TabularRow(
            row_number=row.row_number,
            values=[
                row.values[
                    column_index
                ]
                for column_index
                in columns_to_keep
            ],
        )
        for row in padded
    ]


def _normalize_headers(
    values: list[str],
) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}

    for index, raw_value in enumerate(
        values,
        start=1,
    ):
        value = raw_value.strip()

        if not value:
            value = f"Column {index}"

        key = value.casefold()
        count = seen.get(key, 0) + 1
        seen[key] = count

        if count > 1:
            value = f"{value} ({count})"

        headers.append(value)

    return headers


def _group_rows(
    header: list[str],
    data_rows: list[TabularRow],
) -> list[list[TabularRow]]:
    if not data_rows:
        return [[]]

    groups: list[list[TabularRow]] = []
    current: list[TabularRow] = []

    header_chars = len(
        _table_to_markdown(
            [header]
        )
    )

    current_chars = header_chars

    for row in data_rows:
        row_chars = len(
            _markdown_row(
                row.values
            )
        ) + 1

        would_exceed_rows = (
            len(current)
            >= MAX_DATA_ROWS_PER_ELEMENT
        )

        would_exceed_chars = (
            current
            and
            current_chars
            + row_chars
            > MAX_TABLE_CHARS
        )

        if (
            would_exceed_rows
            or
            would_exceed_chars
        ):
            groups.append(current)
            current = []
            current_chars = header_chars

        current.append(row)
        current_chars += row_chars

    if current:
        groups.append(current)

    return groups


def _table_to_markdown(
    rows: list[list[str]],
) -> str:
    if not rows:
        return ""

    width = max(
        len(row)
        for row in rows
    )

    normalized = [
        row
        + [""] * (
            width
            - len(row)
        )
        for row in rows
    ]

    lines = [
        _markdown_row(
            normalized[0]
        ),
        "| "
        + " | ".join(
            "---"
            for _ in range(width)
        )
        + " |",
    ]

    lines.extend(
        _markdown_row(row)
        for row in normalized[1:]
    )

    return "\n".join(lines)


def _markdown_row(
    row: list[str],
) -> str:
    return (
        "| "
        + " | ".join(
            _escape_markdown_cell(value)
            for value in row
        )
        + " |"
    )


def _escape_markdown_cell(
    value: str,
) -> str:
    return (
        value
        .replace("\\", "\\\\")
        .replace("|", r"\|")
        .replace("\r\n", "<br>")
        .replace("\r", "<br>")
        .replace("\n", "<br>")
    )


def _row_is_blank(
    values: list[Any],
) -> bool:
    return not any(
        normalize_cell_value(value)
        for value in values
    )


def normalize_cell_value(
    value: Any,
) -> str:
    if value is None:
        return ""

    if isinstance(value, bool):
        return (
            "true"
            if value
            else "false"
        )

    if isinstance(
        value,
        datetime,
    ):
        return value.isoformat(
            sep=" ",
            timespec="seconds",
        )

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat(
            timespec="seconds"
        )

    if isinstance(value, Decimal):
        return format(value, "f")

    if isinstance(value, float):
        if math.isnan(value):
            return ""

        if math.isinf(value):
            return str(value)

        if value.is_integer():
            return str(int(value))

        return format(value, ".15g")

    value = str(value)

    value = (
        value
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .strip()
    )

    return value
