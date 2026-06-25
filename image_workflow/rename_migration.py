from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
import csv
import json
import re
import shutil
import time

from .naming import IMAGE_EXTENSIONS, build_original_filename, build_result_filename, safe_token
import argparse


IMAGE_DIR_NAMES = {"商品原始照片", "模型选中", "模型排除", "需人工复核", "最终结果", "人工标注结果"}
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".md", ".txt"}


@dataclass
class MigrationStats:
    planned_renames: int = 0
    renamed_files: int = 0
    updated_text_files: int = 0
    missing_files: int = 0
    skipped_roots: int = 0


def migrate_workspace(root: str | Path, *, dry_run: bool = True) -> MigrationStats:
    workspace = Path(root)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    report_dir = workspace / "migration_reports" / f"image_name_url_sync_{stamp}"
    stats = MigrationStats()
    rows: list[dict[str, str]] = []
    mappings: dict[str, str] = {}

    for product_dir in _formal_product_dirs(workspace / "商品标注结果"):
        product_rows, product_mappings = _plan_formal_product(workspace, product_dir)
        rows.extend(product_rows)
        mappings.update(product_mappings)
    training_root = workspace / "模型训练数据"
    if training_root.exists():
        training_rows, training_mappings = _plan_training_dataset(training_root)
        rows.extend(training_rows)
        mappings.update(training_mappings)

    stats.planned_renames = len(rows)
    if not dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)
        _write_mapping(report_dir / "rename_mapping.csv", rows)
        stats.renamed_files = _apply_file_renames(workspace, rows)
        stats.updated_text_files = _update_text_references(workspace, mappings, report_dir)
    else:
        report_dir.mkdir(parents=True, exist_ok=True)
        _write_mapping(report_dir / "dry_run_rename_mapping.csv", rows)
    stats.missing_files = sum(1 for row in rows if row["status"] == "missing")
    return stats


def _formal_product_dirs(result_root: Path) -> list[Path]:
    if not result_root.exists():
        return []
    return sorted(path.parent for path in result_root.rglob("manifest.csv") if path.parent.name != "商品原始照片")


def _plan_formal_product(workspace: Path, product_dir: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    manifest = product_dir / "manifest.csv"
    if not manifest.exists():
        return [], {}
    with open(manifest, newline="", encoding="utf-8-sig") as handle:
        manifest_rows = list(csv.DictReader(handle))
    source_mapping = _source_mapping_from_manifest(manifest_rows)
    rows: list[dict[str, str]] = []
    mappings: dict[str, str] = {}
    for old_name, new_name in source_mapping.items():
        for folder in ("商品原始照片", "模型选中", "模型排除", "需人工复核"):
            old_path = product_dir / folder / old_name
            new_path = product_dir / folder / new_name
            _append_rename(rows, mappings, workspace, old_path, new_path)
    scores = _read_csv(product_dir / "model_scores.csv")
    for index, row in enumerate(scores, 1):
        old_source = row.get("source_name", "")
        new_source = source_mapping.get(old_source)
        old_result = row.get("result_filename", "")
        if not new_source or not old_result:
            continue
        new_result = _new_result_name(old_result, new_source, index)
        _append_rename(rows, mappings, workspace, product_dir / "最终结果" / old_result, product_dir / "最终结果" / new_result)
    return rows, mappings


def _source_mapping_from_manifest(rows: list[dict[str, str]]) -> dict[str, str]:
    used: set[str] = set()
    mapping: dict[str, str] = {}
    stamp = int(time.time() * 1000)
    for index, row in enumerate(rows):
        old = row.get("filename", "")
        if not old:
            continue
        base = build_original_filename(int(row.get("row_number") or 0), row.get("url", ""), row.get("source", ""))
        new = _unique_name(base, used, stamp + index)
        used.add(new)
        mapping[old] = new
    return mapping


def _new_result_name(old_result: str, new_source: str, fallback_rank: int) -> str:
    match = re.match(r"^((\d{2})_([^_]+(?:_[^_]+)*)__([0-9]{3})__)", old_result)
    if match:
        angle_index = int(match.group(2))
        angle_key = match.group(3)
        rank = int(match.group(4))
        return build_result_filename(angle_index, angle_key, rank, new_source)
    return build_result_filename(((fallback_rank - 1) % 6) + 1, "migrated", fallback_rank, new_source)


def _plan_training_dataset(training_root: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    manifest = training_root / "manifest_all.jsonl"
    if not manifest.exists():
        return [], {}
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows: list[dict[str, str]] = []
    mappings: dict[str, str] = {}
    used_by_dir: dict[Path, set[str]] = {}
    stamp = int(time.time() * 1000)
    for index, record in enumerate(records):
        base = _url_basename(record.get("image_url", ""))
        for key in ("image_path", "original_image_path", "annotation_result_path"):
            value = record.get(key)
            if not value:
                continue
            old_path = Path(value)
            if old_path.name == base:
                continue
            directory = old_path.parent
            used = used_by_dir.setdefault(directory, {path.name for path in (training_root.parent / directory).glob("*") if path.is_file()})
            new_name = _unique_name(base, used, stamp + index)
            used.add(new_name)
            new_path = directory / new_name
            _append_rename(rows, mappings, training_root.parent, training_root.parent / old_path, training_root.parent / new_path)
    return rows, mappings


def _url_basename(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = safe_token(Path(path).name)
    if name and Path(name).suffix.lower() in IMAGE_EXTENSIONS:
        return name
    return "image.jpg"


def _unique_name(filename: str, used: set[str], stamp: int) -> str:
    if filename not in used:
        return filename
    path = Path(filename)
    stem = path.stem or "image"
    suffix = path.suffix or ".jpg"
    candidate = f"{stem}_{stamp}{suffix}"
    while candidate in used:
        stamp += 1
        candidate = f"{stem}_{stamp}{suffix}"
    return candidate


def _append_rename(rows: list[dict[str, str]], mappings: dict[str, str], workspace: Path, old_path: Path, new_path: Path) -> None:
    if old_path == new_path:
        return
    status = "ready" if old_path.exists() else "missing"
    old_rel = str(old_path.relative_to(workspace))
    new_rel = str(new_path.relative_to(workspace))
    rows.append({"status": status, "old_path": old_rel, "new_path": new_rel})
    mappings[old_rel] = new_rel
    mappings[old_path.name] = new_path.name


def _apply_file_renames(workspace: Path, rows: list[dict[str, str]]) -> int:
    count = 0
    for row in sorted(rows, key=lambda item: len(item["old_path"]), reverse=True):
        if row["status"] != "ready":
            continue
        old_path = workspace / row["old_path"]
        new_path = workspace / row["new_path"]
        if not old_path.exists():
            continue
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.replace(new_path)
        count += 1
    return count


def _update_text_references(workspace: Path, mappings: dict[str, str], report_dir: Path) -> int:
    updated = 0
    name_mapping = {Path(old).name: Path(new).name for old, new in mappings.items()}
    token_re = re.compile(r"[A-Za-z0-9_.-]+(?:__[A-Za-z0-9_.-]+)+\.(?:jpg|jpeg|png|webp|bmp|gif)")
    text_files = [path for path in workspace.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES and "migration_reports" not in path.parts]
    for path in text_files:
        original = path.read_text(encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        changed = token_re.sub(lambda match: name_mapping.get(match.group(0), match.group(0)), original)
        if changed == original:
            continue
        backup = report_dir / "text_backups" / path.relative_to(workspace)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        path.write_text(changed, encoding="utf-8-sig" if path.suffix == ".csv" else "utf-8")
        updated += 1
    return updated


def sync_text_from_mapping(root: str | Path, mapping_file: str | Path) -> int:
    workspace = Path(root)
    mapping_path = Path(mapping_file)
    if not mapping_path.is_absolute():
        mapping_path = workspace / mapping_path
    mappings: dict[str, str] = {}
    with open(mapping_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            mappings[row["old_path"]] = row["new_path"]
            mappings[Path(row["old_path"]).name] = Path(row["new_path"]).name
    report_dir = mapping_path.parent
    return _update_text_references(workspace, mappings, report_dir)


def _write_mapping(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["status", "old_path", "new_path"])
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync and verify image filenames against source image URLs.")
    parser.add_argument("--root", default=".", help="Workspace root.")
    parser.add_argument("--apply", action="store_true", help="Apply renames. Without this flag, dry-run verifies the workflow naming standard.")
    parser.add_argument("--sync-text-from", help="Only update text references from an existing rename_mapping.csv.")
    args = parser.parse_args()
    if args.sync_text_from:
        print(json.dumps({"updated_text_files": sync_text_from_mapping(args.root, args.sync_text_from)}, ensure_ascii=False, indent=2))
        return
    stats = migrate_workspace(args.root, dry_run=not args.apply)
    print(json.dumps(stats.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
