from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from .training_roles import PRODUCT_DATA_ROOT, link_or_copy


RUN_ROOT = "模型运行结果"
PREDICT_POSITIVE = "模型选中"
PREDICT_NEGATIVE = "模型排除"
MISMATCH = "不匹配"


def default_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_model_run_images(dataset_dir: Path, predictions: list[dict], run_id: str) -> dict:
    product_data_root = dataset_dir / PRODUCT_DATA_ROOT
    _archive_legacy_run_root(dataset_dir)
    _cleanup_bucket_split_dirs(product_data_root, run_id)
    counts = {PREDICT_POSITIVE: 0, PREDICT_NEGATIVE: 0, MISMATCH: 0}
    for row in predictions:
        source = Path(row["image_path"])
        if not source.exists():
            continue
        bucket = PREDICT_POSITIVE if row["prediction"] == 1 else PREDICT_NEGATIVE
        _link_prediction(source, product_data_root, run_id, bucket, row)
        counts[bucket] += 1
        if not row["matched"]:
            _link_prediction(source, product_data_root, run_id, MISMATCH, row)
            counts[MISMATCH] += 1
    return {"run_id": run_id, "run_root": str(product_data_root / "*" / RUN_ROOT / run_id), "counts": counts}


def _link_prediction(source: Path, product_data_root: Path, run_id: str, bucket: str, row: dict) -> None:
    destination = product_data_root / row["outward_code"] / RUN_ROOT / run_id / bucket / source.name
    link_or_copy(source, destination)


def _cleanup_bucket_split_dirs(product_data_root: Path, run_id: str) -> None:
    for bucket in (PREDICT_POSITIVE, PREDICT_NEGATIVE, MISMATCH):
        for split in ("train", "val", "test"):
            for path in product_data_root.glob(f"*/{RUN_ROOT}/{run_id}/{bucket}/{split}"):
                shutil.rmtree(path)


def _archive_legacy_run_root(dataset_dir: Path) -> None:
    legacy = dataset_dir / RUN_ROOT
    if not legacy.exists():
        return
    archive_root = dataset_dir / "_legacy"
    archive_root.mkdir(parents=True, exist_ok=True)
    base = archive_root / f"{RUN_ROOT}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    target = base
    index = 1
    while target.exists():
        target = archive_root / f"{base.name}_{index}"
        index += 1
    shutil.move(str(legacy), str(target))
