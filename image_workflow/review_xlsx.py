from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


NS_URI = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = f"{{{NS_URI}}}"
STATUS_HEADER = "人工标注状态"
DEFAULT_STATUS_COLUMN = "F"

ET.register_namespace("", NS_URI)


@dataclass(frozen=True)
class WorkbookProductSummary:
    product_codes: set[str]
    all_standard_product_codes: set[str]
    standard_counts: dict[str, int]
    cutout_counts: dict[str, int]


def read_review_statuses(workbook: str | Path, target_keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    status_col = _status_column(workbook)
    shared = _shared_strings(workbook)
    statuses: dict[tuple[str, str], str] = {}
    with ZipFile(workbook) as zf:
        for name in _sheet_names(zf):
            with zf.open(name) as handle:
                for _, row in ET.iterparse(handle, events=("end",)):
                    if not row.tag.endswith("row"):
                        continue
                    values = _row_values(row, shared)
                    key = (values.get("A", ""), values.get("B", ""))
                    status = values.get(status_col, "").strip()
                    if key in target_keys and status:
                        statuses[key] = status
                    row.clear()
    return statuses


def read_workbook_product_codes(workbook: str | Path) -> set[str]:
    return read_workbook_product_summary(workbook).product_codes


def read_workbook_product_summary(workbook: str | Path) -> WorkbookProductSummary:
    shared = _shared_strings(workbook)
    product_codes: set[str] = set()
    urls_by_code: dict[str, set[str]] = {}
    standard_urls_by_code: dict[str, set[str]] = {}
    cutout_urls_by_code: dict[str, set[str]] = {}
    with ZipFile(workbook) as zf:
        for name in _sheet_names(zf):
            with zf.open(name) as handle:
                for _, row in ET.iterparse(handle, events=("end",)):
                    if not row.tag.endswith("row"):
                        continue
                    values = _row_values(row, shared)
                    code = values.get("A", "").strip()
                    image_url = values.get("B", "").strip()
                    source = values.get("C", "").strip()
                    if code and not (code == "outward_code" and image_url == "image_url"):
                        product_codes.add(code)
                        urls_by_code.setdefault(code, set())
                        standard_urls_by_code.setdefault(code, set())
                        cutout_urls_by_code.setdefault(code, set())
                        if image_url:
                            urls_by_code[code].add(image_url)
                            if _is_standard_record(image_url, source):
                                standard_urls_by_code[code].add(image_url)
                            else:
                                cutout_urls_by_code[code].add(image_url)
                    row.clear()
    all_standard = {
        code
        for code, urls in urls_by_code.items()
        if urls and len(standard_urls_by_code.get(code, set())) == len(urls)
    }
    standard_counts = {code: len(standard_urls_by_code.get(code, set())) for code in product_codes}
    cutout_counts = {code: len(cutout_urls_by_code.get(code, set())) for code in product_codes}
    return WorkbookProductSummary(product_codes, all_standard, standard_counts, cutout_counts)


def _is_standard_record(image_url: str, source: str) -> bool:
    source_value = source.strip().lower()
    if "standard" in source_value:
        return True
    if "cutout" in source_value:
        return False
    return "standard" in image_url.lower()


def _status_column(workbook: str | Path) -> str:
    shared = _shared_strings(workbook)
    with ZipFile(workbook) as zf, zf.open("xl/worksheets/sheet1.xml") as handle:
        for _, row in ET.iterparse(handle, events=("end",)):
            if not row.tag.endswith("row"):
                continue
            values = _row_values(row, shared)
            row.clear()
            for column, value in values.items():
                if value == STATUS_HEADER:
                    return column
            return DEFAULT_STATUS_COLUMN
    return DEFAULT_STATUS_COLUMN


def _sheet_names(zf: ZipFile) -> list[str]:
    return sorted(
        (name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")),
        key=lambda name: int(Path(name).stem.replace("sheet", "") or 0),
    )


def _shared_strings(workbook: str | Path) -> list[str]:
    with ZipFile(workbook) as zf:
        if "xl/sharedStrings.xml" not in zf.namelist():
            return []
        with zf.open("xl/sharedStrings.xml") as handle:
            root = ET.parse(handle).getroot()
    return ["".join(node.itertext()) for node in root.findall(f"{NS}si")]


def _row_values(row, shared: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for cell in row:
        if not cell.tag.endswith("c"):
            continue
        ref = cell.attrib.get("r", "")
        column = "".join(char for char in ref if char.isalpha())
        values[column] = _cell_text(cell, shared)
    return values


def _cell_text(cell, shared: list[str]) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{NS}t"))
    value = cell.find(f"{NS}v")
    if value is None or value.text is None:
        return ""
    if cell.attrib.get("t") == "s":
        index = int(value.text)
        return shared[index] if index < len(shared) else ""
    return value.text
