from __future__ import annotations

import numpy as np


def choose_gate(labels: np.ndarray, scores: np.ndarray, target_accuracy: float = 0.955) -> dict:
    best: dict | None = None
    for low_index in range(0, 81):
        for high_index in range(max(low_index + 1, 20), 101):
            low, high = low_index / 100, high_index / 100
            result = evaluate_gate(labels, scores, low, high)
            if result["accuracy"] >= target_accuracy and (best is None or result["coverage"] > best["coverage"]):
                best = result
    return best or evaluate_gate(labels, scores, 0.0, 1.0)


def evaluate_gate(labels: np.ndarray, scores: np.ndarray, low: float, high: float) -> dict:
    predicted = np.full(len(labels), -1, dtype=int)
    predicted[scores <= low] = 0
    predicted[scores >= high] = 1
    decided = predicted != -1
    errors = int((predicted[decided] != labels[decided]).sum()) if decided.any() else 0
    decided_count = int(decided.sum())
    return {
        "low_threshold": low,
        "high_threshold": high,
        "coverage": decided_count / max(1, len(labels)),
        "accuracy": 0.0 if decided_count == 0 else 1 - errors / decided_count,
        "decided_count": decided_count,
        "review_count": int(len(labels) - decided_count),
        "errors": errors,
    }
