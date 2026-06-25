from __future__ import annotations

from pathlib import Path
import csv


SCORE_FIELDS = [
    "source_name",
    "model_score",
    "prediction",
    "bucket",
    "selected_final",
    "result_filename",
    "hard_flags",
    "soft_flags",
    "quality_score",
    "file_size_bytes",
    "width",
    "height",
    "perceptual_hash",
    "phone_component_ratio",
    "phone_bbox_ratio",
    "phone_component_elongation",
    "other_color_ratio",
    "other_color_component_ratio",
    "target_primary_component_count",
    "target_primary_largest_ratio",
    "target_primary_second_ratio",
    "target_secondary_component_ratio",
]


def write_scores(path: str | Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SCORE_FIELDS})
