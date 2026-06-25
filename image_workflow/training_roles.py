from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil


ORIGINAL_ROLE = "商品原始照片"
RESULT_ROLE = "人工标注结果"
LEGACY_RESULT_ROLE = "商品标注结果"
PRODUCT_DATA_ROOT = "商品数据"


def attach_dataset_roles(row: dict, image_path: Path, output_dir: Path) -> dict:
    product_root = output_dir / PRODUCT_DATA_ROOT / row["outward_code"]
    original_path = product_root / ORIGINAL_ROLE / image_path.name
    result_path = product_root / RESULT_ROLE / image_path.name
    roles = [ORIGINAL_ROLE]
    annotation_result_path = None
    if row["label"] == 1:
        roles.append(RESULT_ROLE)
        annotation_result_path = str(result_path)
    if row["download_status"] != "failed" and image_path.exists():
        link_or_copy(image_path, original_path)
        if row["label"] == 1:
            link_or_copy(image_path, result_path)
    return {
        **row,
        "dataset_roles": roles,
        "original_image_path": str(original_path),
        "annotation_result_path": annotation_result_path,
    }


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def cleanup_role_split_dirs(output_dir: Path) -> None:
    for role_dir in (output_dir / PRODUCT_DATA_ROOT).glob("*/*"):
        if role_dir.name not in {ORIGINAL_ROLE, RESULT_ROLE}:
            continue
        for split in ("train", "val", "test"):
            path = role_dir / split
            if path.exists():
                shutil.rmtree(path)


def archive_legacy_business_dirs(output_dir: Path) -> None:
    for name in (ORIGINAL_ROLE, RESULT_ROLE, LEGACY_RESULT_ROLE):
        _archive_dir(output_dir / name, output_dir)


def _archive_dir(path: Path, output_dir: Path) -> None:
    if not path.exists():
        return
    archive_root = output_dir / "_legacy"
    archive_root.mkdir(parents=True, exist_ok=True)
    base = archive_root / f"{path.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    target = base
    index = 1
    while target.exists():
        target = archive_root / f"{base.name}_{index}"
        index += 1
    shutil.move(str(path), str(target))
