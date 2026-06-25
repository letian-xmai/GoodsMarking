from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import csv
import json

from .full_evaluation_preview import write_mismatch_preview
from .full_evaluation_report import write_full_evaluation_report
from .model_run_outputs import default_run_id, write_model_run_images


QUALITY_REASONS = [
    ("is_low_file_size", "文件小于10000 bytes或接近低文件大小阈值"),
    ("is_low_resolution", "图片像素过低，商品难以辨认"),
    ("is_underexposed", "画面过暗，商品难以辨认"),
    ("is_white_background", "疑似白底/建模图特征"),
    ("is_edge_cropped", "商品边缘疑似被裁切"),
    ("has_multiple_products", "疑似画面包含多个商品"),
    ("is_low_boundary_contrast", "商品和背景区分度低"),
    ("is_incomplete_product", "疑似主体不完整"),
    ("has_repeated_product_parts", "疑似重复商品局部/连续帧"),
    ("is_blurry", "疑似模糊"),
    ("is_tiny_subject", "主体占比偏小"),
]


def evaluate_full_testset(dataset_dir: str | Path, model_dir: str | Path, *, write_preview: bool = True, run_id: str | None = None) -> dict:
    dataset_path = Path(dataset_dir)
    model_path = Path(model_dir)
    rows = _read_jsonl(dataset_path / "manifest_all.jsonl")
    model = json.loads((model_path / "calibrated_hash_model.json").read_text(encoding="utf-8"))
    predictions = [_predict(row, model["hash_labels"], dataset_path) for row in rows]
    mismatches = [row for row in predictions if not row["matched"]]
    product_rows = _product_summary(predictions)
    paths = _output_paths(model_path)
    run = write_model_run_images(dataset_path, predictions, run_id or default_run_id())
    _write_jsonl(paths["predictions"], predictions)
    _write_csv(paths["mismatches"], _mismatch_fields(), mismatches)
    _write_csv(paths["products"], _product_fields(), product_rows)
    summary = _summary(predictions, product_rows, paths)
    summary["model_run"] = run
    write_full_evaluation_report(paths["report"], summary, product_rows, mismatches)
    if write_preview:
        summary["preview_written"] = write_mismatch_preview(paths["preview"], mismatches)
    return summary


def _predict(row: dict, hash_labels: dict, dataset_path: Path) -> dict:
    metrics = row.get("quality_metrics") or {}
    phash = str(metrics.get("perceptual_hash") or "")
    entry = hash_labels.get(phash) or {}
    prediction = int(entry.get("label", 0))
    label = int(row["label"])
    image_path = _resolve_image_path(row.get("image_path", ""), dataset_path)
    output = {
        "sample_id": row["sample_id"],
        "outward_code": row["outward_code"],
        "image_url": row.get("image_url", ""),
        "image_path": str(image_path),
        "label": label,
        "prediction": prediction,
        "matched": prediction == label,
        "split": row.get("split", ""),
        "source_types": ",".join(row.get("source_types", [])),
        "row_numbers": ",".join(str(value) for value in row.get("row_numbers", [])),
        "perceptual_hash": phash,
        "hash_positive": int(entry.get("positive", 0)),
        "hash_negative": int(entry.get("negative", 0)),
        "hash_total": int(entry.get("total", 0)),
        "hash_majority_ratio": _majority_ratio(entry),
    }
    output["reason"] = "" if output["matched"] else _reason(row, output)
    return output


def _resolve_image_path(value: str, dataset_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    direct = Path.cwd() / path
    if direct.exists():
        return direct
    dataset_relative = dataset_path / path
    return dataset_relative if dataset_relative.exists() else dataset_path.parent / path


def _majority_ratio(entry: dict) -> float:
    total = int(entry.get("total", 0))
    return 0.0 if total == 0 else max(int(entry.get("positive", 0)), int(entry.get("negative", 0))) / total


def _reason(row: dict, prediction: dict) -> str:
    reasons = []
    if prediction["hash_positive"] and prediction["hash_negative"]:
        reasons.append("同一感知哈希簇存在人工正负标签冲突，模型按多数标签回放")
    if prediction["label"] == 1:
        reasons.append("人工为选中，但同哈希簇多数为备选，可能是边界合格样本、近重复帧或人工口径不一致")
    else:
        reasons.append("人工为备选，但同哈希簇多数为选中，可能与选中图高度近似、人工漏选或重复帧口径不一致")
    flags = _quality_flags(row.get("quality_metrics"))
    if flags:
        reasons.append("图片质量标志：" + "、".join(flags))
    if set(row.get("source_types", [])) == {"备选", "选中"}:
        reasons.append("同一 outward_code+image_url 同时出现备选和选中，聚合后按选中处理")
    return "；".join(reasons)


def _quality_flags(metrics: dict | None) -> list[str]:
    if not metrics:
        return ["quality_metrics_missing"]
    flags = [text for key, text in QUALITY_REASONS if metrics.get(key)]
    size = metrics.get("file_size_bytes")
    if isinstance(size, (int, float)) and size < 10000 and not any("10000 bytes" in flag for flag in flags):
        flags.append("文件小于10KB")
    return flags


def _product_summary(predictions: list[dict]) -> list[dict]:
    stats = defaultdict(Counter)
    for row in predictions:
        stat = stats[row["outward_code"]]
        stat["total"] += 1
        stat["matched"] += int(row["matched"])
        stat["mismatches"] += int(not row["matched"])
        stat["positives"] += int(row["label"] == 1)
        stat["negatives"] += int(row["label"] == 0)
        stat["false_positive"] += int(row["label"] == 0 and row["prediction"] == 1)
        stat["false_negative"] += int(row["label"] == 1 and row["prediction"] == 0)
    return [
        {"outward_code": code, **stat, "match_rate": stat["matched"] / stat["total"]}
        for code, stat in sorted(stats.items())
    ]


def _summary(predictions: list[dict], products: list[dict], paths: dict[str, Path]) -> dict:
    counts = Counter((row["label"], row["prediction"]) for row in predictions)
    matched = sum(1 for row in predictions if row["matched"])
    return {
        "products": len(products),
        "samples": len(predictions),
        "matched": matched,
        "mismatches": len(predictions) - matched,
        "accuracy": matched / max(1, len(predictions)),
        "tp": counts[(1, 1)],
        "fp": counts[(0, 1)],
        "tn": counts[(0, 0)],
        "fn": counts[(1, 0)],
        "mismatch_products": sum(1 for row in products if row["mismatches"]),
        "paths": {key: str(value) for key, value in paths.items()},
    }


def _output_paths(model_path: Path) -> dict[str, Path]:
    return {
        "predictions": model_path / "full_testset_predictions.jsonl",
        "mismatches": model_path / "full_testset_mismatches.csv",
        "products": model_path / "full_testset_product_summary.csv",
        "report": model_path / "full_testset_match_report.md",
        "preview": model_path / "full_testset_mismatch_preview.jpg",
    }


def _mismatch_fields() -> list[str]:
    return ["outward_code", "sample_id", "split", "label", "prediction", "image_path", "image_url", "source_types", "row_numbers", "perceptual_hash", "hash_positive", "hash_negative", "hash_total", "hash_majority_ratio", "reason"]


def _product_fields() -> list[str]:
    return ["outward_code", "total", "matched", "mismatches", "match_rate", "positives", "negatives", "false_positive", "false_negative"]


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
