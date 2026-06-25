from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import unquote, urlparse
import csv
import hashlib
import shutil

from .review_xlsx import WorkbookProductSummary, apply_review_statuses, read_review_statuses as read_xlsx_review_statuses, read_workbook_product_summary


VALID_STATUS = "合格"
INVALID_STATUS = "不合格"
REVIEWED_STATUSES = {VALID_STATUS, INVALID_STATUS}
STATUS_HEADER = "人工标注状态"
STATUS_SIDECAR_SUFFIX = "_人工标注状态.csv"


@dataclass
class ReviewImage:
    review_id: str
    outward_code: str
    result_filename: str
    image_path: str
    image_url: str
    review_status: str = ""
    reviewable: bool = True
    is_raw: bool = False


@dataclass
class ReviewProduct:
    outward_code: str
    images: list[ReviewImage]
    raw_images: list[ReviewImage]
    has_final_dir: bool


@dataclass
class ReviewState:
    products: list[ReviewProduct]
    metrics: dict[str, int]
    image_by_id: dict[str, ReviewImage]
    product_codes: set[str]
    invalid_product_codes: set[str]
    workbook_summary: WorkbookProductSummary | None


class ReviewWorkbench:
    def __init__(self, result_root: str | Path, workbook: str | Path | None = None, batch_size: int = 40, status_file: str | Path | None = None):
        self.result_root = Path(result_root)
        self.workbook = Path(workbook) if workbook else None
        self.status_file = Path(status_file) if status_file else self.workbook
        self.batch_size = batch_size
        self._state: ReviewState | None = None
        self._state_lock = Lock()
        self._load_done = Event()
        self._loading = False
        self._load_error: str | None = None
        self._submit_lock = Lock()

    def current_state(self) -> ReviewState:
        with self._state_lock:
            if self._state is not None:
                return self._state
            build_here = not self._loading
            if build_here:
                self._loading = True
                self._load_error = None
                self._load_done.clear()
        if build_here:
            try:
                state = self.build_state()
            except Exception as exc:
                with self._state_lock:
                    self._load_error = str(exc)
                    self._loading = False
                    self._load_done.set()
                raise
            with self._state_lock:
                self._state = state
                self._loading = False
                self._load_error = None
                self._load_done.set()
                return self._state
        self._load_done.wait()
        with self._state_lock:
            if self._state is not None:
                return self._state
            error = self._load_error or "review workbench state failed to load"
        raise RuntimeError(error)

    def state_snapshot(self, blocking: bool = True) -> ReviewState | None:
        with self._state_lock:
            if self._state is not None:
                return self._state
            if not blocking:
                if not self._loading:
                    self._loading = True
                    self._load_error = None
                    self._load_done.clear()
                    Thread(target=self._load_state_background, daemon=True).start()
                return None
        return self.current_state()

    def _load_state_background(self) -> None:
        try:
            state = self.build_state()
        except Exception as exc:
            with self._state_lock:
                self._load_error = str(exc)
                self._loading = False
                self._load_done.set()
            return
        with self._state_lock:
            self._state = state
            self._loading = False
            self._load_error = None
            self._load_done.set()

    def build_state(self) -> ReviewState:
        summary = read_workbook_product_summary(self.workbook) if self.workbook and self.workbook.exists() else None
        product_codes = summary.product_codes if summary else set()
        invalid_product_codes = summary.all_standard_product_codes if summary else set()
        products = self._scan_products()
        if product_codes:
            products = [product for product in products if product.outward_code in product_codes]
        keys = {
            (image.outward_code, image.image_url)
            for product in products
            for image in [*product.images, *product.raw_images]
            if image.image_url
        }
        statuses = read_review_statuses(self.status_file, keys) if self.status_file else {}
        image_by_id: dict[str, ReviewImage] = {}
        for product in products:
            for image in [*product.images, *product.raw_images]:
                image.review_status = statuses.get((image.outward_code, image.image_url), "")
                image_by_id[image.review_id] = image
        return ReviewState(products, _metrics(products, product_codes, invalid_product_codes), image_by_id, product_codes, invalid_product_codes, summary)

    def next_batch(self, state: ReviewState | None = None) -> list[ReviewImage]:
        state = state or self.current_state()
        product = self.next_unfinished_product(state)
        if product is not None:
            return product.images[:self.batch_size]
        return []

    def next_unfinished_product(self, state: ReviewState | None = None) -> ReviewProduct | None:
        state = state or self.current_state()
        for product in state.products:
            if product_summary_status(product, product.outward_code in state.invalid_product_codes) in {"待标注", "标注中"}:
                return product
        return None

    def product_by_code(self, outward_code: str, state: ReviewState | None = None) -> ReviewProduct | None:
        state = state or self.current_state()
        return next((product for product in state.products if product.outward_code == outward_code), None)

    def product_summaries(self) -> list[dict[str, object]]:
        state = self.current_state()
        product_by_code = {product.outward_code: product for product in state.products}
        codes = sorted(state.product_codes or set(product_by_code))
        rows = []
        for code in codes:
            product = product_by_code.get(code)
            images = [] if product is None else product.images
            rows.append({
                "outward_code": code,
                "standard_count": _workbook_count(state.workbook_summary, "standard", code),
                "cutout_count": _workbook_count(state.workbook_summary, "cutout", code),
                "final_count": len(images),
                "manual_count": sum(1 for image in images if image.review_status == VALID_STATUS),
                "status": product_summary_status(product, code in state.invalid_product_codes),
                "action": "去标注",
            })
        return rows

    def submit_batch(self, review_ids: list[str], invalid_ids: set[str]) -> dict[str, object]:
        with self._submit_lock:
            state = self.current_state()
            updates: dict[tuple[str, str], str] = {}
            image_updates: list[tuple[ReviewImage, str]] = []
            for review_id in review_ids:
                image = state.image_by_id.get(review_id)
                if image is None or not image.reviewable or not image.image_url:
                    continue
                status = INVALID_STATUS if review_id in invalid_ids else VALID_STATUS
                updates[(image.outward_code, image.image_url)] = status
                image_updates.append((image, status))
            _append_review_statuses(self.status_file, updates)
            for image, status in image_updates:
                image.review_status = status
            state.metrics = _metrics(state.products, state.product_codes, state.invalid_product_codes)
            return {"updated": len(updates), "metrics": state.metrics, "next_batch_count": len(self.next_batch(state))}

    def submit_product_statuses(self, statuses_by_id: dict[str, str]) -> dict[str, object]:
        with self._submit_lock:
            state = self.current_state()
            updates: dict[tuple[str, str], str] = {}
            image_updates: list[tuple[ReviewImage, str]] = []
            for review_id, status in statuses_by_id.items():
                image = state.image_by_id.get(review_id)
                clean_status = str(status).strip()
                if image is None or not image.reviewable or not image.image_url or clean_status not in {"", VALID_STATUS, INVALID_STATUS}:
                    continue
                updates[(image.outward_code, image.image_url)] = clean_status
                image_updates.append((image, clean_status))
            _append_review_statuses(self.status_file, updates)
            product_by_code = {product.outward_code: product for product in state.products}
            for image, status in image_updates:
                image.review_status = status
                if status == VALID_STATUS and image.is_raw:
                    promoted = _promote_raw_image_to_final(product_by_code.get(image.outward_code), image)
                    if promoted is not None:
                        promoted.review_status = status
                        state.image_by_id[promoted.review_id] = promoted
            state.metrics = _metrics(state.products, state.product_codes, state.invalid_product_codes)
            return {"updated": len(updates), "metrics": state.metrics, "next_batch_count": len(self.next_batch(state))}

    def _scan_products(self) -> list[ReviewProduct]:
        if not self.result_root.exists():
            return []
        products = []
        for product_dir in sorted(path for path in self.result_root.iterdir() if path.is_dir() and path.name != "bak"):
            final_dir = product_dir / "最终结果"
            raw_dir = product_dir / "商品原始照片"
            images = _scan_images(product_dir, final_dir) if final_dir.exists() else []
            raw_images = _scan_raw_images(product_dir, raw_dir) if raw_dir.exists() else []
            products.append(ReviewProduct(product_dir.name, images, raw_images, final_dir.exists()))
        return products


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _scan_images(product_dir: Path, final_dir: Path) -> list[ReviewImage]:
    manifest = _manifest_urls(product_dir / "manifest.csv")
    for filename, url in _manifest_urls(product_dir / "商品原始照片" / "manifest.csv").items():
        manifest.setdefault(filename, url)
    score_sources = _score_sources(product_dir / "model_scores.csv")
    images = []
    for image_path in sorted(final_dir.glob("*.jpg")):
        source_name = score_sources.get(image_path.name, _source_from_result_name(image_path.name, manifest))
        image_url = _manifest_url(source_name, manifest)
        images.append(ReviewImage(_review_id(product_dir.name, image_url, image_path.name), product_dir.name, image_path.name, str(image_path), image_url))
    return images


def _scan_raw_images(product_dir: Path, raw_dir: Path) -> list[ReviewImage]:
    manifest = _manifest_urls(raw_dir / "manifest.csv")
    images = []
    for image_path in _image_files(raw_dir):
        image_url = _manifest_url(image_path.name, manifest)
        images.append(ReviewImage(_review_id(product_dir.name, image_url, f"raw:{image_path.name}"), product_dir.name, image_path.name, str(image_path), image_url, is_raw=True))
    return images


def _image_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)


def _manifest_urls(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as handle:
        return {row.get("filename", ""): row.get("url", "") for row in csv.DictReader(handle)}


def _score_sources(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        return {row.get("result_filename", ""): row.get("source_name", "") for row in rows if _truthy(row.get("selected_final", ""))}


def _source_from_result_name(result_name: str, manifest: dict[str, str]) -> str:
    stem = Path(result_name).stem.split("__", 2)[-1]
    for filename in manifest:
        if Path(filename).stem == stem:
            return filename
    return stem


def _manifest_url(name: str, manifest: dict[str, str]) -> str:
    if name in manifest:
        return manifest[name]
    stem = Path(name).stem
    for filename, url in manifest.items():
        if Path(filename).stem == stem or _url_stem(url) == stem:
            return url
    return ""


def _url_stem(url: str) -> str:
    return Path(unquote(urlparse(url).path)).stem


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "是"}


def _review_id(outward_code: str, image_url: str, result_filename: str) -> str:
    return hashlib.sha1(f"{outward_code}|{image_url}|{result_filename}".encode("utf-8")).hexdigest()[:16]


def _promote_raw_image_to_final(product: ReviewProduct | None, image: ReviewImage) -> ReviewImage | None:
    if product is None or any(item.image_url == image.image_url for item in product.images):
        return None
    source = Path(image.image_path)
    if not source.exists():
        return None
    final_dir = source.parent.parent / "最终结果"
    final_dir.mkdir(exist_ok=True)
    target = _manual_final_path(final_dir, image.result_filename)
    shutil.copy2(source, target)
    product.has_final_dir = True
    final_image = ReviewImage(_review_id(image.outward_code, image.image_url, target.name), image.outward_code, target.name, str(target), image.image_url)
    product.images.append(final_image)
    product.images.sort(key=lambda item: item.result_filename)
    return final_image


def _manual_final_path(final_dir: Path, source_name: str) -> Path:
    index = len(_image_files(final_dir)) + 1
    serial = 1
    while True:
        candidate = final_dir / f"{index:02d}_manual__{serial:03d}__{Path(source_name).name}"
        if not candidate.exists():
            return candidate
        serial += 1


def read_review_statuses(status_source: str | Path | None, target_keys: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    if status_source is None:
        return {}
    source = Path(status_source)
    statuses = {} if source.suffix.lower() == ".csv" or not source.exists() else read_xlsx_review_statuses(source, target_keys)
    sidecar = _review_status_sidecar_path(source)
    if not sidecar.exists():
        return statuses
    with open(sidecar, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("outward_code", ""), row.get("image_url", ""))
            if key not in target_keys:
                continue
            status = row.get(STATUS_HEADER, "").strip()
            if status:
                statuses[key] = status
            else:
                statuses.pop(key, None)
    return statuses


def _append_review_statuses(status_source: str | Path | None, updates: dict[tuple[str, str], str]) -> None:
    if not updates:
        return
    if status_source is None:
        raise ValueError("status_source is required when writing review statuses")
    path = _review_status_sidecar_path(status_source)
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["outward_code", "image_url", STATUS_HEADER])
        if write_header:
            writer.writeheader()
        for (outward_code, image_url), status in updates.items():
            writer.writerow({"outward_code": outward_code, "image_url": image_url, STATUS_HEADER: status})


def _review_status_sidecar_path(workbook: str | Path) -> Path:
    workbook_path = Path(workbook)
    if workbook_path.suffix.lower() == ".csv":
        return workbook_path
    return workbook_path.with_name(f"{workbook_path.stem}{STATUS_SIDECAR_SUFFIX}")


def _metrics(products: list[ReviewProduct], product_codes: set[str] | None = None, invalid_product_codes: set[str] | None = None) -> dict[str, int]:
    product_by_code = {product.outward_code: product for product in products}
    codes = product_codes or set(product_by_code)
    standard_invalid = invalid_product_codes or set()
    total, completed, invalid, pending_annotation = len(codes), 0, 0, 0
    for code in codes:
        if code in standard_invalid:
            invalid += 1
            continue
        product = product_by_code.get(code)
        if product is None:
            continue
        if _is_review_invalid(product):
            invalid += 1
        elif _is_completed(product):
            completed += 1
        elif _is_pending_annotation(product):
            pending_annotation += 1
    return {
        "total_products": total,
        "completed_products": completed,
        "invalid_products": invalid,
        "pending_annotation_products": pending_annotation,
        "unfinished_products": total - completed - invalid,
    }


def _is_completed(product: ReviewProduct) -> bool:
    return bool(product.images) and all(image.review_status in REVIEWED_STATUSES for image in product.images) and any(image.review_status == VALID_STATUS for image in product.images)


def _is_pending_annotation(product: ReviewProduct) -> bool:
    return bool(product.images) and all(not image.review_status for image in product.images)


def product_summary_status(product: ReviewProduct | None, invalid: bool = False) -> str:
    if invalid:
        return "无效商品"
    if product is None or not product.images:
        return "无最终结果"
    reviewed_count = sum(1 for image in product.images if image.review_status in REVIEWED_STATUSES)
    if reviewed_count == 0:
        return "待标注"
    if reviewed_count < len(product.images):
        return "标注中"
    return "已完成"


def product_status(product: ReviewProduct, invalid_product_codes: set[str] | None = None) -> str:
    if invalid_product_codes and product.outward_code in invalid_product_codes:
        return "无效商品"
    summary_status = product_summary_status(product)
    if summary_status != "已完成":
        return summary_status
    if _is_completed(product):
        return "已完成"
    if _is_review_invalid(product):
        return "已完成"
    return summary_status


def _is_review_invalid(product: ReviewProduct) -> bool:
    if not product.images:
        return False
    return all(image.review_status in REVIEWED_STATUSES for image in product.images) and all(image.review_status == INVALID_STATUS for image in product.images)


def _workbook_count(summary: WorkbookProductSummary | None, count_type: str, code: str) -> int:
    if summary is None:
        return 0
    if count_type == "standard":
        return summary.standard_counts.get(code, 0)
    return summary.cutout_counts.get(code, 0)
