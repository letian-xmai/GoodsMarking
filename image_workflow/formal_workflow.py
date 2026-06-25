from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
import csv
import json
import shutil

import numpy as np
from .annotation_model import _features, _sigmoid
from .contact_sheet import write_contact_sheet
from .downloader import Fetcher, download_group
from .excel_reader import ExcelRecord
from .formal_outputs import write_scores
from .naming import build_result_filename
from .quality import ImageMetrics, analyze_image
from .selection import ANGLES
from .target_consistency import TargetProfile, build_target_profile, target_consistency
from .verification import verify_group

ORIGINAL_ROLE = "商品原始照片"
SELECTED_DIR = "模型选中"
REJECTED_DIR = "模型排除"
REVIEW_DIR = "需人工复核"
FINAL_DIR = "最终结果"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
SKIPPED_ALL_STANDARD = "skipped_all_standard"
ALL_STANDARD_SKIP_REASON = "all_image_urls_contain_standard"
ALL_STANDARD_PROGRESS = "跳过：整组下载链接均为standard参考图"
IMAGE_DECISION_FIELDS = ["row_number", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "skip_reason"]
HARD_FLAGS = [
    "is_white_background",
    "is_low_file_size",
    "is_underexposed",
    "has_multiple_products",
    "is_low_boundary_contrast",
    "is_incomplete_product",
    "has_repeated_product_parts",
]
SOFT_FLAGS = ["is_blurry", "is_tiny_subject", "is_edge_cropped"]


def process_formal_group(
    records: Iterable[ExcelRecord],
    result_root: str | Path,
    model_path: str | Path,
    *,
    target_count: int = 40,
    fetcher: Fetcher | None = None,
    download_workers: int = 4,
) -> dict:
    rows = list(records)
    outward_code = rows[0].outward_code
    result_dir = _prepare_result_dir(Path(result_root), outward_code)
    if _all_urls_are_standard(rows):
        decisions = _all_standard_decisions(rows)
        downloaded = {"total_urls": len(rows), "downloaded_count": 0, "failed_count": 0, "complete": True}
        report = {
            **_report(outward_code, downloaded, SKIPPED_ALL_STANDARD, 0, target_count, []),
            "download_skipped": True,
            "skip_reason": ALL_STANDARD_SKIP_REASON,
            "image_decisions": decisions,
        }
        _write_selection_report(result_dir, report)
        _write_qa_summary(result_dir, report)
        _write_image_decisions(result_dir / "image_decisions.csv", decisions)
        return {**report, "download_complete": True, "verified": True}
    original_dir = result_dir / ORIGINAL_ROLE
    downloaded = download_group(rows, original_dir, workers=download_workers, fetcher=fetcher)
    if not downloaded["complete"]:
        return _report(outward_code, downloaded, "download_incomplete", 0, target_count, [])
    model = _load_model(model_path)
    reference_names = _reference_names(original_dir / "manifest.csv")
    scored = _score_group(original_dir, model, build_target_profile(original_dir, reference_names), reference_names)
    selected = _copy_model_outputs(scored, result_dir, target_count)
    shutil.copy2(original_dir / "manifest.csv", result_dir / "manifest.csv")
    write_scores(result_dir / "model_scores.csv", scored)
    report = _report(outward_code, downloaded, _status(len(selected), target_count), len(selected), target_count, selected)
    _write_selection_report(result_dir, report)
    _write_qa_summary(result_dir, report)
    write_contact_sheet(result_dir / "contact_sheet.jpg", result_dir / FINAL_DIR)
    verified = verify_group(outward_code, original_dir, result_dir / FINAL_DIR, len(rows), target_count)
    _move_verification(result_dir)
    return {**report, "download_complete": downloaded["complete"], "verified": verified["ok"]}


def _prepare_result_dir(result_root: Path, outward_code: str) -> Path:
    result_dir = result_root / outward_code
    if result_dir.exists() and any(result_dir.iterdir()):
        backup_root = result_root / "bak"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = backup_root / f"{outward_code}_{stamp}"
        index = 1
        while target.exists():
            target = backup_root / f"{outward_code}_{stamp}_{index}"
            index += 1
        shutil.move(str(result_dir), str(target))
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir

def _load_model(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _all_urls_are_standard(rows: list[ExcelRecord]) -> bool:
    return bool(rows) and all("standard" in record.image_url.lower() for record in rows)


def _all_standard_decisions(rows: list[ExcelRecord]) -> list[dict[str, str]]:
    return [
        {
            "row_number": str(record.row_number),
            "image_url": record.image_url,
            "source": record.source,
            "图片处理进度": ALL_STANDARD_PROGRESS,
            "最终结果是否包含该图片": "否",
            "skip_reason": ALL_STANDARD_SKIP_REASON,
        }
        for record in rows
    ]


def _reference_names(manifest_path: Path) -> set[str]:
    if not manifest_path.exists():
        return set()
    with open(manifest_path, newline="", encoding="utf-8") as handle:
        return {
            row["filename"]
            for row in csv.DictReader(handle)
            if row.get("filename") and row.get("source", "").lower() == "standard"
        }


def _score_group(original_dir: Path, model: dict, profile: TargetProfile | None, reference_names: set[str] | None = None) -> list[dict]:
    rows = []
    for path in sorted(original_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        metrics = analyze_image(path)
        reference = path.name in reference_names if reference_names is not None else "standard" in path.name.lower()
        score = 0.0 if reference else _model_score(path, metrics, model)
        prediction = int(score >= float(model["threshold"]) and not reference)
        target = None if reference else target_consistency(path, profile)
        rows.append(_score_row(path, metrics, score, prediction, reference, target))
    return sorted(rows, key=lambda row: row["model_score"], reverse=True)
def _model_score(path: Path, metrics: ImageMetrics, model: dict) -> float:
    row = {"image_path": str(path), "quality_metrics": asdict(metrics)}
    features = np.asarray(_features(row, int(model["thumbnail_size"])), dtype=np.float64)
    values = (features - np.asarray(model["mean"])) / np.asarray(model["std"])
    weights = np.asarray(model["weights"], dtype=np.float64)
    return float(_sigmoid(np.r_[1.0, values] @ weights))

def _score_row(path: Path, metrics: ImageMetrics, score: float, prediction: int, reference: bool, target: dict | None) -> dict:
    target = target or {"flags": []}
    hard = _flags(metrics, HARD_FLAGS) + list(target["flags"])
    soft = _flags(metrics, SOFT_FLAGS)
    bucket = REVIEW_DIR if reference else SELECTED_DIR if prediction else REJECTED_DIR
    return {
        "source_name": path.name,
        "source_path": str(path),
        "model_score": score,
        "prediction": prediction,
        "bucket": bucket,
        "hard_flags": ",".join(hard),
        "soft_flags": ",".join(soft),
        "phone_component_ratio": target.get("phone_component_ratio", ""),
        "phone_bbox_ratio": target.get("phone_bbox_ratio", ""),
        "phone_component_elongation": target.get("phone_component_elongation", ""),
        "other_color_ratio": target.get("other_color_ratio", ""),
        "other_color_component_ratio": target.get("other_color_component_ratio", ""),
        "target_primary_component_count": target.get("target_primary_component_count", ""),
        "target_primary_largest_ratio": target.get("target_primary_largest_ratio", ""),
        "target_primary_second_ratio": target.get("target_primary_second_ratio", ""),
        "target_secondary_component_ratio": target.get("target_secondary_component_ratio", ""),
        **asdict(metrics),
    }
def _flags(metrics: ImageMetrics, names: list[str]) -> list[str]:
    return [name for name in names if getattr(metrics, name)]

def _copy_model_outputs(scored: list[dict], result_dir: Path, target_count: int) -> list[dict]:
    for name in (SELECTED_DIR, REJECTED_DIR, REVIEW_DIR, FINAL_DIR):
        (result_dir / name).mkdir(parents=True, exist_ok=True)
    selected, seen_hashes = [], []
    for row in scored:
        source = Path(row["source_path"])
        candidate = row["bucket"] != REVIEW_DIR and not row["hard_flags"] and len(selected) < target_count
        duplicate = any(_hamming(row["perceptual_hash"], other) <= 4 for other in seen_hashes)
        if candidate and not duplicate:
            rank = len(selected) + 1
            angle_index = ((rank - 1) % len(ANGLES)) + 1
            result_name = build_result_filename(angle_index, ANGLES[angle_index - 1], rank, source.name)
            shutil.copy2(source, result_dir / FINAL_DIR / result_name)
            row["bucket"] = SELECTED_DIR
            row["selected_final"] = True
            row["result_filename"] = result_name
            selected.append(row)
            seen_hashes.append(row["perceptual_hash"])
        else:
            row["bucket"] = REVIEW_DIR if row["bucket"] == REVIEW_DIR else REJECTED_DIR
            row["selected_final"] = False
            row["result_filename"] = ""
        shutil.copy2(source, result_dir / row["bucket"] / source.name)
    return selected


def _report(outward_code: str, downloaded: dict, status: str, selected_count: int, target_count: int, selected: list[dict]) -> dict:
    return {
        "outward_code": outward_code,
        "status": status,
        "selection_status": status,
        "target_count": target_count,
        "total_urls": downloaded.get("total_urls", downloaded["downloaded_count"] + downloaded["failed_count"]),
        "downloaded_count": downloaded["downloaded_count"],
        "failed_count": downloaded["failed_count"],
        "selected_count": selected_count,
        "shortfall": max(0, target_count - selected_count),
        "selected": [{"source_name": row["source_name"], "model_score": row["model_score"], "result_filename": row["result_filename"]} for row in selected],
    }


def _status(selected_count: int, target_count: int) -> str:
    return "complete" if selected_count >= target_count else "shortfall"


def _write_selection_report(result_dir: Path, report: dict) -> None:
    (result_dir / "selection_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_image_decisions(path: Path, rows: list[dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMAGE_DECISION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_qa_summary(result_dir: Path, report: dict) -> None:
    (result_dir / "qa_summary.txt").write_text(
        "\n".join(f"{key}: {report[key]}" for key in ("outward_code", "status", "selected_count", "shortfall")) + "\n",
        encoding="utf-8",
    )


def _move_verification(result_dir: Path) -> None:
    source = result_dir / FINAL_DIR / "verification_report.json"
    if source.exists():
        source.replace(result_dir / "verification_report.json")


def _hamming(left: str, right: str) -> int:
    return sum(1 for a, b in zip(left, right) if a != b)
