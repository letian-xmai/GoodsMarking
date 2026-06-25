from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .phone_detection import phone_presence


@dataclass(frozen=True)
class TargetProfile:
    allowed_hues: np.ndarray
    primary_hues: np.ndarray


def build_target_profile(original_dir: str | Path, reference_names: set[str] | None = None) -> TargetProfile | None:
    counts = np.zeros(256, dtype=np.int64)
    paths = sorted(Path(original_dir).iterdir())
    for path in paths:
        if reference_names is not None and path.name not in reference_names:
            continue
        if reference_names is None and "standard" not in path.name.lower():
            continue
        counts += _hue_counts(path)
    if int(counts.sum()) < 80:
        return None
    threshold = max(12, int(counts.sum() * 0.006))
    allowed = counts >= threshold
    primary = counts >= max(threshold, int(counts.max() * 0.45))
    expanded = allowed.copy()
    primary_expanded = primary.copy()
    for offset in range(-10, 11):
        expanded |= np.roll(allowed, offset)
    for offset in range(-8, 9):
        primary_expanded |= np.roll(primary, offset)
    return TargetProfile(expanded, primary_expanded)


def target_consistency(path: str | Path, profile: TargetProfile | None) -> dict[str, object]:
    phone = phone_presence(path)
    if profile is None:
        return phone
    hue, saturation, value = _hsv(path)
    colored = (saturation > 45) & (value > 35)
    allowed = profile.allowed_hues[hue]
    primary = colored & profile.primary_hues[hue]
    other = colored & ~allowed
    other_ratio = float(other.mean())
    other_component = _largest_component_ratio(other)
    primary_count, primary_largest, primary_second = _component_stats(primary)
    primary_largest_ratio = primary_largest / float(primary.size)
    secondary = colored & allowed & ~profile.primary_hues[hue] & ~_expand(primary, 12)
    secondary_component = _largest_component_ratio(secondary)
    flags = list(phone["flags"])
    if _has_other_product_color(other_ratio, other_component):
        flags.append("has_other_product_color")
    if _has_multiple_target_instances(primary_count, primary_second, secondary_component):
        flags.append("has_multiple_target_instances")
    if _has_low_target_occupancy(primary_largest_ratio, secondary_component):
        flags.append("is_low_target_occupancy")
    return {
        "flags": flags,
        "phone_component_ratio": phone.get("phone_component_ratio", ""),
        "phone_bbox_ratio": phone.get("phone_bbox_ratio", ""),
        "phone_component_elongation": phone.get("phone_component_elongation", ""),
        "other_color_ratio": f"{other_ratio:.6f}",
        "other_color_component_ratio": f"{other_component:.6f}",
        "target_primary_component_count": primary_count,
        "target_primary_largest_ratio": f"{primary_largest_ratio:.6f}",
        "target_primary_second_ratio": f"{primary_second:.6f}",
        "target_secondary_component_ratio": f"{secondary_component:.6f}",
    }


def _has_multiple_target_instances(primary_count: int, primary_second: float, secondary_component: float) -> bool:
    return primary_count >= 2 and primary_second > 0.075


def _has_other_product_color(other_ratio: float, other_component: float) -> bool:
    return other_ratio > 0.55 and other_component > 0.45


def _has_low_target_occupancy(primary_largest: float, secondary_component: float) -> bool:
    return primary_largest < 0.10 and secondary_component > 0.35


def _hue_counts(path: Path) -> np.ndarray:
    hue, saturation, value = _hsv(path)
    useful = (saturation > 45) & (value > 35) & ~((saturation < 30) & (value > 235))
    return np.bincount(hue[useful].ravel(), minlength=256)


def _hsv(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with Image.open(path) as image:
        hsv = image.convert("RGB").resize((160, 160)).convert("HSV")
        arr = np.asarray(hsv, dtype=np.uint8)
    return arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]


def _largest_component_ratio(mask: np.ndarray) -> float:
    _, largest, _ = _component_stats(mask)
    return largest / float(mask.size)


def _component_stats(mask: np.ndarray) -> tuple[int, int, float]:
    seen = np.zeros(mask.shape, dtype=bool)
    sizes = []
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if mask[y, x] and not seen[y, x]:
                sizes.append(_component_size(mask, seen, y, x))
    large = sorted((size for size in sizes if size >= 120), reverse=True)
    largest = large[0] if large else 0
    second_ratio = 0.0 if len(large) < 2 else large[1] / float(height * width)
    return len(large), largest, second_ratio


def _expand(mask: np.ndarray, radius: int) -> np.ndarray:
    grown = mask.copy()
    for _ in range(radius):
        source = grown
        grown = source.copy()
        grown[1:, :] |= source[:-1, :]
        grown[:-1, :] |= source[1:, :]
        grown[:, 1:] |= source[:, :-1]
        grown[:, :-1] |= source[:, 1:]
    return grown


def _component_size(mask: np.ndarray, seen: np.ndarray, y: int, x: int) -> int:
    stack = [(y, x)]
    seen[y, x] = True
    count = 0
    while stack:
        cy, cx = stack.pop()
        count += 1
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    return count
