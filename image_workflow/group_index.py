from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
import csv
import shutil

from .excel_reader import ExcelRecord, iter_excel_records
from .naming import safe_token
from .progress import ProgressTable


FIELDS = ["sheet_name", "sheet_index", "row_number", "outward_code", "image_url", "source"]


@dataclass(frozen=True)
class IndexSummary:
    total_records: int
    group_count: int
    index_dir: str


class _WriterCache:
    def __init__(self, root: Path, max_open: int = 128):
        self.root = root
        self.max_open = max_open
        self.handles: OrderedDict[str, tuple] = OrderedDict()

    def writer_for(self, outward_code: str):
        key = str(outward_code)
        if key in self.handles:
            self.handles.move_to_end(key)
            return self.handles[key][1]
        if len(self.handles) >= self.max_open:
            _, (handle, _) = self.handles.popitem(last=False)
            handle.close()
        path = group_file(self.root, key)
        exists = path.exists() and path.stat().st_size > 0
        handle = open(path, "a", newline="", encoding="utf-8")
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        self.handles[key] = (handle, writer)
        return writer

    def close(self) -> None:
        for handle, _ in self.handles.values():
            handle.close()
        self.handles.clear()


def build_group_index(
    xlsx_path: str | Path,
    index_dir: str | Path,
    progress_path: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> IndexSummary:
    root = Path(index_dir)
    group_root = root / "groups"
    if overwrite and root.exists():
        shutil.rmtree(root)
    group_root.mkdir(parents=True, exist_ok=True)
    counts: Counter = Counter()
    writer_cache = _WriterCache(group_root)
    try:
        for record in iter_excel_records(xlsx_path):
            writer_cache.writer_for(record.outward_code).writerow(record_to_row(record))
            counts[record.outward_code] += 1
    finally:
        writer_cache.close()
    _write_group_index(root / "group_index.csv", counts)
    if progress_path:
        ProgressTable(progress_path).initialize_pending(dict(counts))
    return IndexSummary(sum(counts.values()), len(counts), str(root))


def read_group_records(path: str | Path) -> list[ExcelRecord]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [row_to_record(row) for row in csv.DictReader(handle)]


def iter_group_files(index_dir: str | Path) -> list[Path]:
    root = Path(index_dir) / "groups"
    return sorted(root.glob("*.csv"))


def group_file(index_dir: str | Path, outward_code: str) -> Path:
    return Path(index_dir) / f"{safe_token(outward_code)}.csv"


def record_to_row(record: ExcelRecord) -> dict[str, str]:
    return {
        "sheet_name": record.sheet_name,
        "sheet_index": str(record.sheet_index),
        "row_number": str(record.row_number),
        "outward_code": record.outward_code,
        "image_url": record.image_url,
        "source": record.source,
    }


def row_to_record(row: dict[str, str]) -> ExcelRecord:
    return ExcelRecord(
        row["sheet_name"],
        int(row["sheet_index"]),
        int(row["row_number"]),
        row["outward_code"],
        row["image_url"],
        row.get("source", ""),
    )


def _write_group_index(path: Path, counts: Counter) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["outward_code", "total_urls", "record_file"])
        writer.writeheader()
        for outward_code, total in sorted(counts.items()):
            writer.writerow({
                "outward_code": outward_code,
                "total_urls": total,
                "record_file": str(group_file(path.parent / "groups", outward_code)),
            })
