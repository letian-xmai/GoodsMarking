from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable
import csv
import time
import urllib.request

from .excel_reader import ExcelRecord
from .naming import build_original_filename


Fetcher = Callable[[str], bytes]
MANIFEST_FIELDS = ["row_number", "url", "source", "status", "filename", "error"]


def download_group(
    records: Iterable[ExcelRecord],
    original_dir: str | Path,
    *,
    workers: int = 4,
    retries: int = 2,
    timeout: int = 30,
    fetcher: Fetcher | None = None,
) -> dict:
    rows = list(records)
    path = Path(original_dir)
    path.mkdir(parents=True, exist_ok=True)
    fetch = fetcher or (lambda url: fetch_url(url, timeout=timeout))
    filenames = _assign_filenames(rows, path)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_download_one, record, filename, path, retries, fetch): record
            for record, filename in zip(rows, filenames)
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: int(item["row_number"]))
    _write_manifest(path / "manifest.csv", results)
    failed = [row for row in results if row["status"] != "downloaded"]
    return {
        "total_urls": len(rows),
        "downloaded_count": len(rows) - len(failed),
        "failed_count": len(failed),
        "complete": not failed,
        "manifest_path": str(path / "manifest.csv"),
    }


def fetch_url(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "product-image-workflow/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _download_one(
    record: ExcelRecord,
    filename: str,
    directory: Path,
    retries: int,
    fetcher: Fetcher,
) -> dict[str, str]:
    destination = directory / filename
    if destination.exists() and destination.stat().st_size > 0:
        return _manifest_row(record, "downloaded", filename, "")
    error = ""
    for attempt in range(retries + 1):
        try:
            data = fetcher(record.image_url)
            destination.write_bytes(data)
            return _manifest_row(record, "downloaded", filename, "")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    return _manifest_row(record, "failed", filename, error)


def _manifest_row(record: ExcelRecord, status: str, filename: str, error: str) -> dict[str, str]:
    return {
        "row_number": str(record.row_number),
        "url": record.image_url,
        "source": record.source,
        "status": status,
        "filename": filename,
        "error": error,
    }


def _assign_filenames(records: list[ExcelRecord], directory: Path) -> list[str]:
    existing_by_url = _read_manifest_filenames(directory / "manifest.csv")
    used_names = {path.name for path in directory.iterdir() if path.is_file()}
    filenames: list[str] = []
    for record in records:
        existing = existing_by_url.get(record.image_url)
        if existing:
            filenames.append(existing)
            used_names.add(existing)
            continue
        base = build_original_filename(record.row_number, record.image_url, record.source)
        filename = base if base not in used_names else _timestamped_filename(base, used_names)
        filenames.append(filename)
        used_names.add(filename)
    return filenames


def _timestamped_filename(filename: str, used_names: set[str]) -> str:
    path = Path(filename)
    stem = path.stem or "image"
    suffix = path.suffix or ".jpg"
    while True:
        candidate = f"{stem}_{int(time.time() * 1000)}{suffix}"
        if candidate not in used_names:
            return candidate
        time.sleep(0.001)


def _read_manifest_filenames(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as handle:
        return {
            row["url"]: row["filename"]
            for row in csv.DictReader(handle)
            if row.get("url") and row.get("filename")
        }


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
