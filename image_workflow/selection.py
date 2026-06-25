from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import shutil

from .naming import build_result_filename
from .quality import ImageMetrics, analyze_image


ANGLES = ["front_label", "back_barcode", "top_cap", "side_standing", "horizontal_bottle", "handheld_oblique"]


def select_downloaded_group(
    outward_code: str,
    original_dir: str | Path,
    result_dir: str | Path,
    target_count: int = 40,
) -> dict:
    original_path = Path(original_dir)
    result_path = Path(result_dir)
    result_path.mkdir(parents=True, exist_ok=True)
    reference_items = _find_reference_images(original_path)
    strict_candidates, fill_candidates = _rank_candidates(original_path)
    candidates = strict_candidates + fill_candidates
    selected = _dedupe_and_pick(candidates, target_count)
    _clear_previous_results(result_path)
    _copy_reference_images(reference_items, result_path / "reference")
    selected_items = _copy_selected(selected, result_path)
    report = _build_report(outward_code, candidates, selected_items, target_count, reference_items, strict_candidates, fill_candidates)
    _write_report(result_path, report)
    return report

def _rank_candidates(original_dir: Path) -> tuple[list[tuple[ImageMetrics, str, str]], list[tuple[ImageMetrics, str, str]]]:
    strict: list[tuple[ImageMetrics, str, str]] = []
    fill: list[tuple[ImageMetrics, str, str]] = []
    for path in sorted(original_dir.iterdir()):
        if _is_standard_reference(path):
            continue
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
            continue
        try:
            item = analyze_image(path)
        except Exception:
            continue
        if _hard_exclude(item):
            continue
        if item.is_edge_cropped:
            fill.append((item, "fill", "edge_cropped"))
        else:
            strict.append((item, "strict", ""))
    key = lambda row: row[0].quality_score
    return sorted(strict, key=key, reverse=True), sorted(fill, key=key, reverse=True)


def _dedupe_and_pick(candidates: list[tuple[ImageMetrics, str, str]], target_count: int) -> list[tuple[ImageMetrics, str, str]]:
    selected: list[tuple[ImageMetrics, str, str]] = []
    seen_hashes: list[str] = []
    for item, tier, reason in candidates:
        if any(_hamming(item.perceptual_hash, other) <= 4 for other in seen_hashes):
            continue
        selected.append((item, tier, reason))
        seen_hashes.append(item.perceptual_hash)
        if len(selected) >= target_count:
            break
    if len(selected) < min(target_count, len(candidates)):
        selected = candidates[:target_count]
    return selected


def _copy_selected(selected: list[tuple[ImageMetrics, str, str]], result_dir: Path) -> list[dict]:
    items: list[dict] = []
    for rank, (metrics, tier, reason) in enumerate(selected, start=1):
        angle_index = ((rank - 1) % len(ANGLES)) + 1
        angle_key = ANGLES[angle_index - 1]
        destination = result_dir / build_result_filename(angle_index, angle_key, rank, metrics.path)
        shutil.copy2(metrics.path, destination)
        row = asdict(metrics)
        row.update({"rank": rank, "angle": angle_key, "result_filename": destination.name, "selection_tier": tier, "fill_reason": reason})
        items.append(row)
    return items


def _build_report(
    outward_code: str,
    candidates: list[ImageMetrics],
    selected: list[dict],
    target_count: int,
    references: list[ImageMetrics],
    strict_candidates: list[tuple[ImageMetrics, str, str]],
    fill_candidates: list[tuple[ImageMetrics, str, str]],
) -> dict:
    status = "complete" if len(selected) >= target_count else "shortfall"
    return {
        "outward_code": outward_code,
        "status": status,
        "target_count": target_count,
        "candidate_count": len(candidates),
        "strict_candidate_count": len(strict_candidates),
        "fill_candidate_count": len(fill_candidates),
        "selected_count": len(selected),
        "shortfall": max(0, target_count - len(selected)),
        "angle_review_needed": True,
        "quality_rules": _quality_rules(),
        "reference_count": len(references),
        "white_reference_count": sum(1 for item in references if item.is_white_background),
        "reference_images": [asdict(item) for item in references],
        "angle_counts": _angle_counts(selected),
        "selected": selected,
    }


def _write_report(result_dir: Path, report: dict) -> None:
    with open(result_dir / "selection_report.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    with open(result_dir / "qa_summary.txt", "w", encoding="utf-8") as handle:
        handle.write(f"outward_code: {report['outward_code']}\n")
        handle.write(f"status: {report['status']}\n")
        handle.write(f"selected_count: {report['selected_count']}\n")
        handle.write(f"strict_candidate_count: {report['strict_candidate_count']}\n")
        handle.write(f"fill_candidate_count: {report['fill_candidate_count']}\n")
        handle.write(f"reference_count: {report['reference_count']}\n")
        handle.write("angle_review_needed: yes\n")


def _clear_previous_results(result_dir: Path) -> None:
    for path in result_dir.glob("*.jpg"):
        path.unlink()
    reference_dir = result_dir / "reference"
    if reference_dir.exists():
        shutil.rmtree(reference_dir)


def _find_reference_images(original_dir: Path) -> list[ImageMetrics]:
    references: list[ImageMetrics] = []
    for path in sorted(original_dir.iterdir()):
        if not _is_standard_reference(path):
            continue
        try:
            references.append(analyze_image(path))
        except Exception:
            continue
    return references


def _copy_reference_images(references: list[ImageMetrics], reference_dir: Path) -> None:
    if not references:
        return
    reference_dir.mkdir(parents=True, exist_ok=True)
    for item in references:
        source = Path(item.path)
        shutil.copy2(source, reference_dir / source.name)


def _is_standard_reference(path: Path) -> bool:
    return path.is_file() and "standard" in path.name.lower()


def _quality_rules() -> dict:
    return {
        "min_file_size_bytes": 10000,
        "low_resolution_recorded_not_hard_excluded": True,
        "standard_images_role": "reference_only",
        "single_product_required": True,
        "underexposed_excluded": True,
        "incomplete_product_excluded": True,
        "repeated_product_parts_excluded": True,
        "white_background_excluded": True,
        "low_boundary_contrast_excluded": True,
        "strict_first_then_edge_fill": True,
    }


def _hard_exclude(item: ImageMetrics) -> bool:
    return (
        item.is_white_background
        or item.is_tiny_subject
        or item.is_low_file_size
        or item.is_underexposed
        or item.has_multiple_products
        or item.is_low_boundary_contrast
        or item.is_incomplete_product
        or item.has_repeated_product_parts
    )


def _hamming(left: str, right: str) -> int:
    return sum(1 for a, b in zip(left, right) if a != b)


def _angle_counts(selected: list[dict]) -> dict[str, int]:
    counts = {angle: 0 for angle in ANGLES}
    for item in selected:
        counts[item["angle"]] += 1
    return counts
