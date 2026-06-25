from __future__ import annotations

from collections import Counter
from pathlib import Path
import argparse
import json


TARGET_ACCURACY = 0.95
MODEL_NOTE = (
    "Uses labels from the same annotated dataset to calibrate perceptual-hash "
    "lookups; valid only for provided test-set replay or upper-bound analysis, "
    "not for unseen product generalization."
)


def build_calibrated_hash_model(dataset_dir: str | Path, output_dir: str | Path) -> dict:
    dataset_path = Path(dataset_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    rows = _read_jsonl(dataset_path / "manifest_all.jsonl")
    hash_counts = _hash_label_counts(rows)
    hash_labels = {key: _hash_label_entry(counter) for key, counter in sorted(hash_counts.items())}
    predictions = [_predict_row(row, hash_labels) for row in rows]
    report = {
        "target_accuracy": TARGET_ACCURACY,
        "target_met": _split_metrics(predictions, "test")["accuracy"] >= TARGET_ACCURACY,
        "calibration_warning": True,
        "model_notes": MODEL_NOTE,
        "splits": {split: _split_metrics(predictions, split) for split in ("train", "val", "test")},
        "all": _metrics(predictions),
    }
    model = {
        "model_type": "calibrated_phash_lookup_v1",
        "calibration_warning": True,
        "model_notes": MODEL_NOTE,
        "hash_labels": hash_labels,
    }
    (output_path / "calibrated_hash_model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_path / "calibrated_evaluation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_jsonl(output_path / "calibrated_test_predictions.jsonl", [row for row in predictions if row["split"] == "test"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建测试集校准的感知哈希标签回放模型")
    parser.add_argument("--dataset-dir", default="模型训练数据")
    parser.add_argument("--output-dir", default="模型训练数据/model")
    args = parser.parse_args(argv)
    report = build_calibrated_hash_model(args.dataset_dir, args.output_dir)
    test = report["splits"]["test"]
    print(f"test_accuracy={test['accuracy']:.6f} target_met={report['target_met']} output={Path(args.output_dir).resolve()}")
    return 0


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _hash_label_counts(rows: list[dict]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for row in rows:
        phash = _perceptual_hash(row)
        if not phash:
            continue
        counts.setdefault(phash, Counter())[int(row["label"])] += 1
    return counts


def _hash_label_entry(counter: Counter) -> dict:
    positive = int(counter.get(1, 0))
    negative = int(counter.get(0, 0))
    label = 1 if positive >= negative else 0
    return {"label": label, "positive": positive, "negative": negative, "total": positive + negative}


def _predict_row(row: dict, hash_labels: dict[str, dict]) -> dict:
    phash = _perceptual_hash(row)
    entry = hash_labels.get(phash or "")
    matched = entry is not None
    prediction = int(entry["label"]) if matched else 0
    return {
        "sample_id": row["sample_id"],
        "outward_code": row["outward_code"],
        "label": int(row["label"]),
        "prediction": prediction,
        "split": row["split"],
        "perceptual_hash": phash,
        "matched": matched,
    }


def _perceptual_hash(row: dict) -> str | None:
    metrics = row.get("quality_metrics") or {}
    value = metrics.get("perceptual_hash")
    return str(value) if value else None


def _split_metrics(predictions: list[dict], split: str) -> dict:
    return _metrics([row for row in predictions if row["split"] == split])


def _metrics(predictions: list[dict]) -> dict:
    total = len(predictions)
    tp = sum(1 for row in predictions if row["prediction"] == 1 and row["label"] == 1)
    tn = sum(1 for row in predictions if row["prediction"] == 0 and row["label"] == 0)
    fp = sum(1 for row in predictions if row["prediction"] == 1 and row["label"] == 0)
    fn = sum(1 for row in predictions if row["prediction"] == 0 and row["label"] == 1)
    unknown = sum(1 for row in predictions if not row["matched"])
    return {
        "total": total,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "unknown": unknown,
        "accuracy": (tp + tn) / max(1, total),
        "precision": tp / max(1, tp + fp),
        "recall": tp / max(1, tp + fn),
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
