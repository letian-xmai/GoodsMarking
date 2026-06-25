from __future__ import annotations

from collections import Counter
from pathlib import Path
import csv
import json

from .selection import ANGLES, analyze_image


def verify_group(
    outward_code: str,
    original_dir: str | Path,
    result_dir: str | Path,
    expected_original_count: int | None = None,
    target_count: int = 40,
) -> dict:
    original_path = Path(original_dir)
    result_path = Path(result_dir)
    original_images = _image_files(original_path)
    result_images = _image_files(result_path)
    expected = expected_original_count or _manifest_expected_count(original_path)
    expected = expected if expected is not None else len(original_images)
    issues = []
    if len(original_images) != expected:
        issues.append(f"original_count_mismatch:{len(original_images)}!={expected}")
    if len(result_images) != min(target_count, len(result_images)) and len(result_images) > target_count:
        issues.append(f"result_count_too_high:{len(result_images)}>{target_count}")
    if len(result_images) < target_count:
        issues.append(f"shortfall:{target_count - len(result_images)}")
    issues.extend(_duplicate_name_issues(original_images, "original"))
    issues.extend(_duplicate_name_issues(result_images, "result"))
    issues.extend(_result_quality_issues(result_images))
    angle_counts = _result_angle_counts(result_images)
    issues.extend(_angle_issues(angle_counts, target_count))
    report = {
        "outward_code": outward_code,
        "ok": not [issue for issue in issues if not issue.startswith("shortfall:")],
        "issues": issues,
        "expected_original_count": expected,
        "original_count": len(original_images),
        "result_count": len(result_images),
        "target_count": target_count,
        "angle_counts": angle_counts,
    }
    _write_verification(result_path, report)
    return report


def _image_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    return sorted(item for item in path.iterdir() if item.suffix.lower() in exts)


def _manifest_expected_count(path: Path) -> int | None:
    manifest = path / "manifest.csv"
    if not manifest.exists():
        return None
    with open(manifest, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return sum(1 for row in rows if row.get("status") == "downloaded")


def _duplicate_name_issues(paths: list[Path], label: str) -> list[str]:
    counts = Counter(path.name for path in paths)
    return [f"{label}_duplicate_filename:{name}" for name, count in counts.items() if count > 1]


def _result_quality_issues(paths: list[Path]) -> list[str]:
    issues = []
    for path in paths:
        try:
            metrics = analyze_image(path)
        except Exception:
            issues.append(f"result_unreadable:{path.name}")
            continue
        if metrics.is_white_background:
            issues.append(f"result_white_background:{path.name}")
        if metrics.is_low_file_size:
            issues.append(f"result_low_file_size:{path.name}")
        if metrics.is_underexposed:
            issues.append(f"result_underexposed:{path.name}")
        if metrics.has_multiple_products:
            issues.append(f"result_multiple_products:{path.name}")
        if metrics.is_low_boundary_contrast:
            issues.append(f"result_low_boundary_contrast:{path.name}")
        if metrics.is_incomplete_product:
            issues.append(f"result_incomplete_product:{path.name}")
        if metrics.has_repeated_product_parts:
            issues.append(f"result_repeated_product_parts:{path.name}")
    return issues


def _result_angle_counts(paths: list[Path]) -> dict[str, int]:
    counts = {angle: 0 for angle in ANGLES}
    for path in paths:
        for angle in ANGLES:
            if f"_{angle}__" in path.name:
                counts[angle] += 1
                break
    return counts


def _angle_issues(angle_counts: dict[str, int], target_count: int) -> list[str]:
    if target_count < len(ANGLES) * 2:
        return []
    return [
        f"angle_shortfall:{angle}:{count}<2"
        for angle, count in angle_counts.items()
        if count < 2
    ]


def _write_verification(result_path: Path, report: dict) -> None:
    result_path.mkdir(parents=True, exist_ok=True)
    with open(result_path / "verification_report.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
