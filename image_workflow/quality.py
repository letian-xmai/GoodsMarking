from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

@dataclass(frozen=True)
class ImageMetrics:
    path: str
    width: int
    height: int
    brightness: float
    white_ratio: float
    edge_score: float
    subject_ratio: float
    quality_score: float
    perceptual_hash: str
    is_white_background: bool
    is_blurry: bool
    is_tiny_subject: bool
    is_edge_cropped: bool
    file_size_bytes: int
    foreground_contrast: float
    foreground_component_count: int
    second_component_ratio: float
    is_low_file_size: bool
    is_low_resolution: bool
    is_underexposed: bool
    has_multiple_products: bool
    is_low_boundary_contrast: bool
    is_incomplete_product: bool
    has_repeated_product_parts: bool

def analyze_image(path: str | Path) -> ImageMetrics:
    image_path = Path(path)
    with Image.open(image_path) as image:
        width, height = image.size
        rgb = image.convert("RGB").resize((128, 128))
        arr = np.asarray(rgb, dtype=np.float32)
    gray = arr.mean(axis=2)
    file_size = image_path.stat().st_size
    brightness = float(gray.mean())
    white_ratio = float(np.mean(np.all(arr > 245, axis=2)))
    edge_score = _edge_score(gray)
    subject_ratio = _subject_ratio(arr)
    foreground_contrast = _foreground_contrast(arr)
    crop_score = _edge_crop_score(gray)
    image_hash = _average_hash(gray)
    is_white = white_ratio > 0.82 and subject_ratio < 0.35
    is_blurry = edge_score < 2.0
    is_tiny = subject_ratio < 0.04
    component_count, second_component_ratio = _foreground_components(arr)
    is_edge_cropped = min(width, height) < 120 or crop_score > 1.45
    is_low_file_size = file_size < 10000
    is_low_resolution = min(width, height) < 180 and max(width, height) < 260
    is_underexposed = brightness < 70 and white_ratio < 0.03
    has_multiple_products = second_component_ratio > 0.10 and not is_edge_cropped
    is_low_boundary_contrast = foreground_contrast < 55
    is_incomplete_product = _is_incomplete_product(subject_ratio, foreground_contrast, crop_score)
    has_repeated_product_parts = (
        is_edge_cropped and component_count >= 2 and second_component_ratio > 0.025 and foreground_contrast < 85
    )
    quality = edge_score * 8 + subject_ratio * 100 - white_ratio * 90
    if is_white:
        quality -= 200
    if is_blurry:
        quality -= 40
    if is_tiny:
        quality -= 60
    if is_edge_cropped:
        quality -= 180
    if is_low_file_size:
        quality -= 120
    if is_low_resolution:
        quality -= 160
    if is_underexposed:
        quality -= 180
    if has_multiple_products:
        quality -= 180
    if is_low_boundary_contrast:
        quality -= 100
    if is_incomplete_product:
        quality -= 180
    if has_repeated_product_parts:
        quality -= 180
    return ImageMetrics(
        str(image_path),
        width,
        height,
        brightness,
        white_ratio,
        edge_score,
        subject_ratio,
        quality,
        image_hash,
        is_white,
        is_blurry,
        is_tiny,
        is_edge_cropped,
        file_size,
        foreground_contrast,
        component_count,
        second_component_ratio,
        is_low_file_size,
        is_low_resolution,
        is_underexposed,
        has_multiple_products,
        is_low_boundary_contrast,
        is_incomplete_product,
        has_repeated_product_parts,
    )

def _edge_score(gray: np.ndarray) -> float:
    dx = np.abs(np.diff(gray, axis=1)).mean()
    dy = np.abs(np.diff(gray, axis=0)).mean()
    return float(dx + dy)

def _subject_ratio(arr: np.ndarray) -> float:
    flat = arr.reshape(-1, 3)
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]], axis=0)
    background = np.median(border.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(flat - background, axis=1)
    return float(np.mean(distance > 28))

def _foreground_components(arr: np.ndarray) -> tuple[int, float]:
    mask = _foreground_mask(arr)
    seen = np.zeros(mask.shape, dtype=bool)
    components: list[int] = []
    height, width = mask.shape
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            components.append(_component_size(mask, seen, y, x))
    large = sorted((size for size in components if size >= 120), reverse=True)
    second_ratio = 0.0 if len(large) < 2 else large[1] / float(height * width)
    return len(large), second_ratio


def _foreground_contrast(arr: np.ndarray) -> float:
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]], axis=0)
    background = np.median(border.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(arr.reshape(-1, 3) - background, axis=1)
    foreground = distance[distance > 28]
    return 0.0 if foreground.size == 0 else float(foreground.mean())


def _foreground_mask(arr: np.ndarray) -> np.ndarray:
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]], axis=0)
    background = np.median(border.reshape(-1, 3), axis=0)
    distance = np.linalg.norm(arr - background, axis=2)
    return distance > 28


def _is_incomplete_product(subject_ratio: float, foreground_contrast: float, crop_score: float) -> bool:
    closeup_with_border_loss = subject_ratio > 0.78 and crop_score > 1.40
    return closeup_with_border_loss and foreground_contrast < 75

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

def _edge_crop_score(gray: np.ndarray) -> float:
    edges = _edge_mask(gray)
    margin = 12
    border = np.zeros_like(edges, dtype=bool)
    border[:margin, :] = True
    border[-margin:, :] = True
    border[:, :margin] = True
    border[:, -margin:] = True
    return float(edges[border].mean() / (edges.mean() + 1e-6))

def _edge_mask(gray: np.ndarray) -> np.ndarray:
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    edges = np.zeros_like(gray)
    edges[:, 1:] += gx
    edges[1:, :] += gy
    return edges > max(12, float(np.percentile(edges, 85)))

def _average_hash(gray: np.ndarray) -> str:
    small = Image.fromarray(gray.astype(np.uint8)).resize((8, 8), Image.Resampling.BILINEAR)
    values = np.asarray(small, dtype=np.float32)
    return "".join("1" if value > values.mean() else "0" for value in values.flatten())
