from __future__ import annotations

from hashlib import sha1
from pathlib import Path
from urllib.parse import unquote, urlparse
import re


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def build_original_filename(row_number: int, url: str, source: str) -> str:
    path = unquote(urlparse(url).path)
    name = safe_token(Path(path).name)
    if not name or Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        return f"image{extension_from_url(url)}"
    return name


def build_result_filename(angle_index: int, angle_key: str, rank: int, source_name: str) -> str:
    return safe_token(Path(source_name).name)


def extension_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    ext = Path(path).suffix.lower()
    return ext if ext in IMAGE_EXTENSIONS else ".jpg"


def safe_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return cleaned.strip("._-") or "unknown"


def short_hash(value: str, length: int = 12) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:length]
