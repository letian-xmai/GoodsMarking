from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterable
from urllib.request import Request, urlopen
from xml.etree.ElementTree import iterparse
from zipfile import ZipFile
import hashlib
import json
import re
import time

from .labeling_standard import write_product_labeling_standard
from .naming import build_original_filename, short_hash
from .quality import analyze_image
from .training_roles import attach_dataset_roles, archive_legacy_business_dirs, cleanup_role_split_dirs


Fetcher = Callable[[str], bytes]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CELL_RE = re.compile(r"([A-Z]+)(\d+)")
TYPE_ORDER = {"备选": 0, "选中": 1}


@dataclass(frozen=True)
class LabelRecord:
    row_number: int
    outward_code: str
    image_url: str
    type_value: str


@dataclass(frozen=True)
class LabeledSample:
    sample_id: str
    outward_code: str
    image_url: str
    label: int
    source_types: list[str]
    row_numbers: list[int]
    split: str = ""


def parse_label_records(workbook: str | Path) -> Iterable[LabelRecord]:
    with ZipFile(workbook) as zf:
        with zf.open("xl/worksheets/sheet1.xml") as handle:
            for _, row in iterparse(handle, events=("end",)):
                if row.tag != f"{NS}row":
                    continue
                row_number = int(row.attrib["r"])
                values = _row_values(row)
                row.clear()
                if not values or values[0] == "outward_code":
                    continue
                if len(values) >= 3 and values[0] and values[1] and values[2]:
                    yield LabelRecord(row_number, values[0], values[1], values[2])


def build_labeled_samples(records: Iterable[LabelRecord]) -> list[LabeledSample]:
    grouped: dict[tuple[str, str], dict] = {}
    for record in records:
        key = (record.outward_code, record.image_url)
        item = grouped.setdefault(key, {"types": set(), "rows": []})
        item["types"].add(record.type_value)
        item["rows"].append(record.row_number)
    samples = []
    for (code, url), item in grouped.items():
        source_types = sorted(item["types"], key=lambda value: TYPE_ORDER.get(value, 99))
        label = 1 if "选中" in item["types"] else 0
        samples.append(LabeledSample(f"{code}__{short_hash(url)}", code, url, label, source_types, sorted(item["rows"])))
    return sorted(samples, key=lambda sample: (sample.outward_code, sample.image_url))


def assign_group_splits(samples: list[LabeledSample]) -> list[LabeledSample]:
    groups = sorted({sample.outward_code for sample in samples}, key=lambda code: hashlib.sha1(code.encode()).hexdigest())
    train_end = round(len(groups) * 0.8)
    val_end = train_end + round(len(groups) * 0.1)
    split_by_group = {
        code: "train" if index < train_end else "val" if index < val_end else "test"
        for index, code in enumerate(groups)
    }
    return [replace(sample, split=split_by_group[sample.outward_code]) for sample in samples]


def build_training_dataset(
    label_workbook: str | Path,
    output_dir: str | Path,
    *,
    download_workers: int = 4,
    fetcher: Fetcher | None = None,
) -> dict:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    archive_legacy_business_dirs(output_path)
    cleanup_role_split_dirs(output_path)
    samples = assign_group_splits(build_labeled_samples(parse_label_records(label_workbook)))
    rows = _download_and_enrich(samples, output_path, download_workers, fetcher or fetch_url)
    _write_jsonl(output_path / "manifest_all.jsonl", rows)
    for split in ("train", "val", "test"):
        _write_jsonl(output_path / f"{split}.jsonl", [row for row in rows if row["split"] == split])
    write_product_labeling_standard(output_path / "product_labeling_standard.md")
    summary = _summary(rows)
    (output_path / "dataset_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def fetch_url(url: str, timeout: int = 30) -> bytes:
    request = Request(url, headers={"User-Agent": "product-image-training-set/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _download_and_enrich(samples: list[LabeledSample], output_dir: Path, workers: int, fetcher: Fetcher) -> list[dict]:
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_materialize_sample, sample, output_dir, fetcher): sample for sample in samples}
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda row: (row["split"], row["outward_code"], row["image_url"]))


def _materialize_sample(sample: LabeledSample, output_dir: Path, fetcher: Fetcher) -> dict:
    image_path = _sample_image_path(sample, output_dir)
    image_path.parent.mkdir(parents=True, exist_ok=True)
    status, error = "skipped_existing", ""
    if not image_path.exists() or image_path.stat().st_size == 0:
        try:
            image_path.write_bytes(fetcher(sample.image_url))
            status = "downloaded"
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
    metrics = None
    if status != "failed":
        try:
            metrics = asdict(analyze_image(image_path))
        except Exception as exc:
            status, error = "failed", f"{type(exc).__name__}: {exc}"
    row = {
        **asdict(sample),
        "image_path": str(image_path),
        "quality_metrics": metrics,
        "download_status": status,
        "error": error,
    }
    return attach_dataset_roles(row, image_path, output_dir)


def _sample_image_path(sample: LabeledSample, output_dir: Path) -> Path:
    directory = output_dir / "images" / sample.split / sample.outward_code
    filename = build_original_filename(sample.row_numbers[0] if sample.row_numbers else 0, sample.image_url, "")
    path = directory / filename
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    stamp = int(time.time() * 1000)
    while path.exists():
        path = directory / f"{stem}_{stamp}{suffix}"
        stamp += 1
    return path


def _summary(rows: list[dict]) -> dict:
    label_counts = {"positive": 0, "negative": 0}
    split_counts: dict[str, dict[str, int]] = {}
    status_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for row in rows:
        label_counts["positive" if row["label"] == 1 else "negative"] += 1
        for role in row.get("dataset_roles", []):
            role_counts[role] = role_counts.get(role, 0) + 1
        split_counts.setdefault(row["split"], {"total": 0, "positive": 0, "negative": 0})
        split_counts[row["split"]]["total"] += 1
        split_counts[row["split"]]["positive" if row["label"] == 1 else "negative"] += 1
        status_counts[row["download_status"]] = status_counts.get(row["download_status"], 0) + 1
    return {
        "unique_items": len(rows),
        "group_count": len({row["outward_code"] for row in rows}),
        "label_counts": label_counts,
        "dataset_role_counts": role_counts,
        "split_counts": split_counts,
        "download_status_counts": status_counts,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row_values(row) -> list[str]:
    values: list[str] = []
    for cell in row.findall(f"{NS}c"):
        index = _col_index(cell.attrib["r"])
        while len(values) <= index:
            values.append("")
        values[index] = _cell_text(cell)
    return values


def _col_index(ref: str) -> int:
    number = 0
    for char in CELL_RE.match(ref).group(1):
        number = number * 26 + ord(char) - 64
    return number - 1


def _cell_text(cell) -> str:
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{NS}t")).strip()
    value = cell.find(f"{NS}v")
    return "" if value is None or value.text is None else value.text.strip()
