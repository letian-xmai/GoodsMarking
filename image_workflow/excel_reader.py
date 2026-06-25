from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile


CELL_RE = re.compile(r"([A-Z]+)(\d+)")
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class ExcelRecord:
    sheet_name: str
    sheet_index: int
    row_number: int
    outward_code: str
    image_url: str
    source: str


@dataclass(frozen=True)
class WorkbookSummary:
    total_urls: int
    group_counts: Counter
    source_counts: Counter
    sheet_rows: dict[str, int]


def iter_excel_records(xlsx_path: str | Path) -> Iterator[ExcelRecord]:
    path = Path(xlsx_path)
    with ZipFile(path) as zf:
        for sheet_index, sheet_name in enumerate(_sheet_files(zf), start=1):
            yield from _iter_sheet_records(zf, sheet_name, sheet_index)


def records_for_group(xlsx_path: str | Path, outward_code: str) -> list[ExcelRecord]:
    target = str(outward_code)
    return [record for record in iter_excel_records(xlsx_path) if record.outward_code == target]


def inspect_workbook(xlsx_path: str | Path) -> WorkbookSummary:
    group_counts: Counter = Counter()
    source_counts: Counter = Counter()
    sheet_rows: dict[str, int] = Counter()
    total = 0
    for record in iter_excel_records(xlsx_path):
        total += 1
        group_counts[record.outward_code] += 1
        source_counts[record.source or "<MISSING>"] += 1
        sheet_rows[record.sheet_name] += 1
    return WorkbookSummary(total, group_counts, source_counts, dict(sheet_rows))


def _sheet_files(zf: ZipFile) -> list[str]:
    names = [
        name for name in zf.namelist()
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    ]
    return sorted(names, key=_sheet_sort_key)


def _sheet_sort_key(name: str) -> int:
    match = re.search(r"sheet(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _iter_sheet_records(zf: ZipFile, sheet_name: str, sheet_index: int) -> Iterator[ExcelRecord]:
    with zf.open(sheet_name) as handle:
        for _, row in iterparse(handle, events=("end",)):
            if not row.tag.endswith("row"):
                continue
            values = _row_values(row)
            row_number = int(row.attrib.get("r", "0") or 0)
            row.clear()
            if not values:
                continue
            if sheet_index == 1 and row_number == 1 and _looks_like_header(values):
                continue
            record = _record_from_values(sheet_name, sheet_index, row_number, values)
            if record is not None:
                yield record


def _row_values(row) -> dict[int, str]:
    values: dict[int, str] = {}
    for cell in row:
        if not cell.tag.endswith("c"):
            continue
        match = CELL_RE.match(cell.attrib.get("r", ""))
        if not match:
            continue
        values[_column_index(match.group(1))] = _cell_text(cell).strip()
    return values


def _record_from_values(
    sheet_name: str,
    sheet_index: int,
    row_number: int,
    values: dict[int, str],
) -> ExcelRecord | None:
    code = values.get(0, "")
    url = values.get(1, "")
    source = values.get(2, "")
    if not code or not url:
        return None
    return ExcelRecord(sheet_name, sheet_index, row_number, code, url, source)


def _looks_like_header(values: dict[int, str]) -> bool:
    return values.get(0) == "outward_code" and values.get(1) == "image_url"


def _column_index(column: str) -> int:
    index = 0
    for char in column:
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _cell_text(cell) -> str:
    if cell.attrib.get("t") == "inlineStr":
        node = cell.find(".//m:t", NS)
        return "" if node is None or node.text is None else node.text
    value = cell.find("m:v", NS)
    return "" if value is None or value.text is None else value.text
