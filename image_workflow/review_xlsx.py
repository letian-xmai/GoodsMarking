from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile
import shutil


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


def apply_review_statuses(workbook: str | Path, updates: dict[tuple[str, str], str]) -> None:
    if not updates:
        return
    workbook_path = Path(workbook)
    status_col = _status_column(workbook_path)
    shared = _shared_strings(workbook_path)
    with NamedTemporaryFile(delete=False, suffix=".xlsx", dir=str(workbook_path.parent)) as temp:
        temp_path = Path(temp.name)
    try:
        with ZipFile(workbook_path) as src, ZipFile(temp_path, "w", ZIP_DEFLATED) as dst:
            for info in src.infolist():
                with src.open(info.filename) as handle:
                    if info.filename.startswith("xl/worksheets/sheet") and info.filename.endswith(".xml"):
                        with dst.open(info.filename, "w") as out:
                            _rewrite_sheet(handle, out, shared, updates, status_col, info.filename.endswith("sheet1.xml"))
                    else:
                        dst.writestr(info, handle.read())
        shutil.move(str(temp_path), str(workbook_path))
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _rewrite_sheet(source, output, shared: list[str], updates: dict[tuple[str, str], str], status_col: str, has_header: bool) -> None:
    output.write(f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS_URI}"><sheetData>'.encode())
    for _, row in ET.iterparse(source, events=("end",)):
        if not row.tag.endswith("row"):
            continue
        row_number = int(row.attrib.get("r", "0") or 0)
        values = _row_values(row, shared)
        if has_header and row_number == 1 and values.get("A") == "outward_code":
            _set_inline_cell(row, status_col, row_number, STATUS_HEADER)
        else:
            key = (values.get("A", ""), values.get("B", ""))
            if key in updates:
                _set_inline_cell(row, status_col, row_number, updates[key])
        output.write(ET.tostring(row, encoding="utf-8"))
        row.clear()
    output.write(b"</sheetData></worksheet>")


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


def _set_inline_cell(row, column: str, row_number: int, value: str) -> None:
    ref = f"{column}{row_number}"
    cell = next((item for item in row if item.tag.endswith("c") and item.attrib.get("r") == ref), None)
    if cell is None:
        cell = ET.Element(f"{NS}c", {"r": ref, "t": "inlineStr"})
        row.append(cell)
    cell.attrib["t"] = "inlineStr"
    for child in list(cell):
        cell.remove(child)
    inline = ET.SubElement(cell, f"{NS}is")
    text = ET.SubElement(inline, f"{NS}t")
    text.text = value
