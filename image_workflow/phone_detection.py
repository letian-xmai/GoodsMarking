from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def phone_presence(path: str | Path) -> dict[str, object]:
    with Image.open(path) as image:
        arr = np.asarray(image.convert("RGB").resize((180, 180)), dtype=np.uint8)
    gray = arr.mean(axis=2)
    best = _best_dark_component(gray < 70)
    flags = ["has_phone_like_object"] if _looks_like_phone(best) else []
    return {
        "flags": flags,
        "phone_component_ratio": f"{best.get('area_ratio', 0.0):.6f}",
        "phone_bbox_ratio": f"{best.get('bbox_ratio', 0.0):.6f}",
        "phone_component_elongation": f"{best.get('elongation', 0.0):.6f}",
    }


def _looks_like_phone(component: dict[str, float]) -> bool:
    return (
        component.get("area_ratio", 0.0) > 0.055
        and component.get("bbox_ratio", 0.0) > 0.14
        and 0.20 <= component.get("fill_ratio", 0.0) <= 0.68
        and component.get("elongation", 0.0) > 2.5
    )


def _best_dark_component(mask: np.ndarray) -> dict[str, float]:
    seen = np.zeros(mask.shape, dtype=bool)
    best: dict[str, float] = {}
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if mask[y, x] and not seen[y, x]:
                item = _component(mask, seen, y, x)
                if item.get("area_ratio", 0.0) > best.get("area_ratio", 0.0):
                    best = item
    return best


def _component(mask: np.ndarray, seen: np.ndarray, y: int, x: int) -> dict[str, float]:
    stack, points = [(y, x)], []
    seen[y, x] = True
    while stack:
        cy, cx = stack.pop()
        points.append((cy, cx))
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = cy + dy, cx + dx
            if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    ys, xs = np.asarray([p[0] for p in points]), np.asarray([p[1] for p in points])
    box_area = max(1, int((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)))
    coords = np.c_[xs - xs.mean(), ys - ys.mean()]
    values = np.linalg.eigvalsh(np.cov(coords.T)) if len(points) > 1 else np.asarray([1.0, 1.0])
    return {
        "area_ratio": len(points) / float(mask.size),
        "bbox_ratio": box_area / float(mask.size),
        "fill_ratio": len(points) / float(box_area),
        "elongation": float((values[-1] / max(values[0], 1e-6)) ** 0.5),
    }
