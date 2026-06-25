from __future__ import annotations

from pathlib import Path
import argparse
import json
import math

import numpy as np
from PIL import Image

from .confidence_gate import choose_gate, evaluate_gate


METRIC_KEYS = [
    "brightness",
    "white_ratio",
    "edge_score",
    "subject_ratio",
    "quality_score",
    "is_edge_cropped",
    "file_size_bytes",
    "foreground_contrast",
    "foreground_component_count",
    "second_component_ratio",
    "is_low_file_size",
    "has_multiple_products",
    "is_low_boundary_contrast",
    "is_incomplete_product",
    "has_repeated_product_parts",
]


def train_annotation_model(
    dataset_dir: str | Path,
    output_dir: str | Path,
    *,
    epochs: int = 800,
    thumbnail_size: int = 16,
) -> dict:
    dataset_path = Path(dataset_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    rows = _read_manifest(dataset_path / "manifest_all.jsonl")
    x, y, splits = _feature_matrix(rows, thumbnail_size)
    train_mask, val_mask = splits == "train", splits == "val"
    mean, std = x[train_mask].mean(axis=0), x[train_mask].std(axis=0) + 1e-6
    xs = (x - mean) / std
    weights = _fit_logistic(xs[train_mask], y[train_mask], epochs)
    val_scores = _predict_scores(xs[val_mask], weights)
    threshold = _best_threshold(y[val_mask], val_scores)
    model = {
        "model_type": "numpy_logistic_image_quality_v1",
        "metric_keys": METRIC_KEYS,
        "thumbnail_size": thumbnail_size,
        "threshold": threshold,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "weights": weights.tolist(),
    }
    (output_path / "annotation_model.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    report = _evaluation_report(rows, xs, y, splits, weights, threshold)
    (output_path / "evaluation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_predictions(output_path / "test_predictions.jsonl", rows, xs, y, splits, weights, threshold)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练并评估商品图片通用质量模型")
    parser.add_argument("--dataset-dir", default="模型训练数据")
    parser.add_argument("--output-dir", default="模型训练数据/model")
    parser.add_argument("--epochs", type=int, default=800)
    parser.add_argument("--thumbnail-size", type=int, default=16)
    args = parser.parse_args(argv)
    report = train_annotation_model(args.dataset_dir, args.output_dir, epochs=args.epochs, thumbnail_size=args.thumbnail_size)
    test = report["splits"]["test"]
    print(f"test_accuracy={test['accuracy']:.6f} target_met={report['target_met']} output={Path(args.output_dir).resolve()}")
    return 0


def _read_manifest(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _feature_matrix(rows: list[dict], thumbnail_size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    features = [_features(row, thumbnail_size) for row in rows]
    labels = [row["label"] for row in rows]
    splits = [row["split"] for row in rows]
    return np.asarray(features, dtype=np.float64), np.asarray(labels, dtype=np.float64), np.asarray(splits)


def _features(row: dict, thumbnail_size: int) -> list[float]:
    with Image.open(row["image_path"]) as image:
        thumb = image.convert("L").resize((thumbnail_size, thumbnail_size), Image.Resampling.BILINEAR)
        pixels = (np.asarray(thumb, dtype=np.float32) / 255.0).flatten().tolist()
    metrics = row["quality_metrics"]
    values = [float(metrics[key]) for key in METRIC_KEYS]
    values.append(math.log1p(float(metrics["file_size_bytes"])))
    values.append(float(metrics["width"]) / max(1.0, float(metrics["height"])))
    return pixels + values


def _fit_logistic(x: np.ndarray, y: np.ndarray, epochs: int) -> np.ndarray:
    xb = np.c_[np.ones(len(x)), x]
    weights = np.zeros(xb.shape[1])
    positives = max(1.0, float(y.sum()))
    negatives = max(1.0, float(len(y) - y.sum()))
    sample_weights = np.where(y == 1, len(y) / (2 * positives), len(y) / (2 * negatives))
    moment = np.zeros_like(weights)
    velocity = np.zeros_like(weights)
    for step in range(1, epochs + 1):
        scores = _sigmoid(xb @ weights)
        errors = (scores - y) * sample_weights
        regularizer = 2e-3 * np.r_[0.0, weights[1:]]
        gradient = xb.T @ errors / len(y) + regularizer
        moment = 0.9 * moment + 0.1 * gradient
        velocity = 0.999 * velocity + 0.001 * (gradient * gradient)
        weights -= 0.01 * moment / (np.sqrt(velocity) + 1e-8)
    return weights


def _predict_scores(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return _sigmoid(np.c_[np.ones(len(x)), x] @ weights)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -50, 50)))


def _best_threshold(labels: np.ndarray, scores: np.ndarray) -> float:
    best_threshold, best_accuracy = 0.5, -1.0
    for threshold in np.linspace(0, 1, 1001):
        accuracy = float(((scores >= threshold) == (labels == 1)).mean())
        if accuracy > best_accuracy:
            best_threshold, best_accuracy = float(threshold), accuracy
    return best_threshold


def _evaluation_report(rows: list[dict], x: np.ndarray, y: np.ndarray, splits: np.ndarray, weights: np.ndarray, threshold: float) -> dict:
    split_metrics = {split: _metrics(y[splits == split], _predict_scores(x[splits == split], weights), threshold) for split in ("train", "val", "test")}
    val_scores = _predict_scores(x[splits == "val"], weights)
    gate = choose_gate(y[splits == "val"].astype(int), val_scores)
    gate_metrics = {
        split: evaluate_gate(y[splits == split].astype(int), _predict_scores(x[splits == split], weights), gate["low_threshold"], gate["high_threshold"])
        for split in ("train", "val", "test")
    }
    return {
        "target_accuracy": 0.95,
        "target_met": split_metrics["test"]["accuracy"] >= 0.95,
        "confidence_gate": {
            "target_accuracy": 0.95,
            "target_met": gate_metrics["test"]["accuracy"] >= 0.95,
            "splits": gate_metrics,
        },
        "splits": split_metrics,
        "row_count": len(rows),
        "model_notes": "Uses only local image pixels and quality metrics; does not use outward_code, URL, or labels at prediction time.",
    }


def _metrics(labels: np.ndarray, scores: np.ndarray, threshold: float) -> dict:
    predicted = scores >= threshold
    actual = labels == 1
    tp = int((predicted & actual).sum())
    tn = int((~predicted & ~actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    total = int(len(labels))
    return {
        "threshold": threshold,
        "total": total,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / total,
        "precision": tp / max(1, tp + fp),
        "recall": tp / max(1, tp + fn),
    }


def _write_predictions(path: Path, rows: list[dict], x: np.ndarray, y: np.ndarray, splits: np.ndarray, weights: np.ndarray, threshold: float) -> None:
    scores = _predict_scores(x[splits == "test"], weights)
    test_rows = [row for row in rows if row["split"] == "test"]
    with open(path, "w", encoding="utf-8") as handle:
        for row, score in zip(test_rows, scores):
            output = {"sample_id": row["sample_id"], "label": row["label"], "score": float(score), "prediction": int(score >= threshold)}
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
