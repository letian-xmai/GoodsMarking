from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
import json
import mimetypes

from .review_workbench import ReviewImage, ReviewProduct, ReviewWorkbench, model_statuses, product_status, raw_manifest_rows


def run_review_workbench(result_dir: str | Path, workbook: str | Path | None, status_file: str | Path | None = None, state_db: str | Path | None = None, host: str = "127.0.0.1", port: int = 8765, batch_size: int = 40) -> None:
    workbench = ReviewWorkbench(result_dir, workbook, batch_size=batch_size, status_file=status_file, state_db=state_db)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/":
                self._send_text(_HTML, "text/html; charset=utf-8")
            elif path == "/api/products":
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                self._send_json(_products_payload(
                    workbench,
                    page=_int_param(params, "page", 1),
                    page_size=_int_param(params, "page_size", 50),
                    query=params.get("q", [""])[0],
                    blocking=False,
                ))
            elif path == "/api/product":
                parsed = urlparse(self.path)
                code = parse_qs(parsed.query).get("outward_code", [""])[0] or None
                self._send_json(_product_payload(workbench, code, blocking=False))
            elif path == "/api/images":
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                self._send_json(_images_payload(
                    workbench,
                    page=_int_param(params, "page", 1),
                    page_size=_int_param(params, "page_size", 100),
                    query=params.get("q", [""])[0],
                    filter_by=params.get("filter", [""])[0],
                    blocking=False,
                ))
            elif path == "/api/batch":
                self._send_json(_batch_payload(workbench, blocking=False))
            elif path.startswith("/image/"):
                self._send_image(workbench, unquote(path.rsplit("/", 1)[-1]))
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path not in {"/api/submit", "/api/product/submit"}:
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            payload = json.loads(body.decode("utf-8") or "{}")
            if path == "/api/product/submit":
                result = workbench.submit_product_statuses(dict(payload.get("statuses", {})))
                code = str(payload.get("outward_code") or "") or None
                self._send_json({**result, **_product_payload(workbench, code)})
                return
            result = workbench.submit_batch(list(payload.get("review_ids", [])), set(payload.get("invalid_ids", [])))
            self._send_json({**result, **_batch_payload(workbench)})

        def log_message(self, fmt: str, *args) -> None:
            return

        def _send_json(self, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, content_type: str) -> None:
            data = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_image(self, wb: ReviewWorkbench, review_id: str) -> None:
            item = wb.current_state().image_by_id.get(review_id)
            path = Path(item.image_path) if item else Path()
            if not path.exists():
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"review_workbench=http://{host}:{port}")
    server.serve_forever()


def _batch_payload(workbench: ReviewWorkbench, blocking: bool = True) -> dict:
    payload = _product_payload(workbench, blocking=blocking)
    return {**payload, "batch": payload["images"]}


def _products_payload(workbench: ReviewWorkbench, page: int = 1, page_size: int = 50, query: str = "", blocking: bool = True) -> dict:
    clean_query = str(query or "").strip()
    safe_page_size = max(1, page_size)
    if getattr(workbench, "state_db", None):
        return _sqlite_products_payload(workbench, page, safe_page_size, clean_query)
    state = workbench.state_snapshot(blocking=blocking)
    if state is None:
        return {
            "loading": True,
            "metrics": _empty_metrics(),
            "products": [],
            "pagination": {"page": 1, "page_size": safe_page_size, "total": 0, "total_pages": 1, "query": clean_query},
        }
    rows = workbench.product_summaries()
    if clean_query:
        needle = clean_query.lower()
        rows = [row for row in rows if needle in str(row["outward_code"]).lower()]
    total = len(rows)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * safe_page_size
    return {
        "metrics": state.metrics,
        "products": rows[start:start + safe_page_size],
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
            "query": clean_query,
        },
    }


def _sqlite_products_payload(workbench: ReviewWorkbench, page: int, page_size: int, query: str) -> dict:
    total = workbench.state_db.count_product_summary_rows(query)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(max(1, page), total_pages)
    offset = (safe_page - 1) * page_size
    rows = [_sqlite_product_row(row) for row in workbench.state_db.product_summary_rows(query, page_size, offset)]
    return {
        "metrics": workbench.state_db.workflow_metrics(),
        "products": rows,
        "pagination": {
            "page": safe_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "query": query,
        },
    }


def _sqlite_product_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "outward_code": str(row.get("outward_code", "")),
        "standard_image_url": str(row.get("standard_image_url") or ""),
        "standard_count": int(row.get("standard_count") or 0),
        "cutout_count": int(row.get("cutout_count") or 0),
        "final_count": int(row.get("final_count") or 0),
        "manual_count": int(row.get("manual_count") or 0),
        "status": _progress_status_label(str(row.get("status") or "")),
        "action": "去标注",
    }


def _progress_status_label(status: str) -> str:
    return {
        "complete": "已完成",
        "shortfall": "不足40",
        "skipped_all_standard": "无效商品",
        "pending": "待处理",
    }.get(status, status or "无最终结果")


def _images_payload(workbench: ReviewWorkbench, page: int = 1, page_size: int = 100, query: str = "", filter_by: str = "", blocking: bool = True) -> dict:
    clean_query = str(query or "").strip()
    clean_filter = str(filter_by or "").strip()
    safe_page_size = max(1, page_size)
    if getattr(workbench, "state_db", None):
        return _sqlite_images_payload(workbench, workbench.state_db.workflow_metrics(), clean_query, clean_filter, page, safe_page_size)
    state = workbench.state_snapshot(blocking=blocking)
    if state is None:
        return {
            "loading": True,
            "metrics": _empty_metrics(),
            "image_metrics": _empty_image_metrics(),
            "images": [],
            "pagination": {"page": 1, "page_size": safe_page_size, "total": 0, "total_pages": 1, "query": clean_query, "filter": clean_filter},
        }
    rows = _all_image_rows(workbench, state)
    image_metrics = _image_metrics(workbench, clean_query, len(rows))
    if clean_query:
        rows = [row for row in rows if row["outward_code"] == clean_query]
    rows = _filter_image_rows(rows, clean_filter)
    total = len(rows)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * safe_page_size
    return {
        "metrics": state.metrics,
        "image_metrics": image_metrics,
        "images": rows[start:start + safe_page_size],
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
            "query": clean_query,
            "filter": clean_filter,
        },
    }


def _sqlite_images_payload(workbench: ReviewWorkbench, metrics: dict, query: str, filter_by: str, page: int, page_size: int) -> dict:
    manual_status = "合格" if filter_by == "qualified" else ""
    model_status = "模型选中" if filter_by == "model_final" else ""
    total = workbench.state_db.count_product_image_rows(query, manual_status, model_status)
    total_pages = max(1, (total + page_size - 1) // page_size)
    safe_page = min(max(1, page), total_pages)
    offset = (safe_page - 1) * page_size
    image_rows = workbench.state_db.product_image_rows(query, manual_status, model_status, page_size, offset)
    rows = [_sqlite_image_row(row) for row in image_rows]
    return {
        "metrics": metrics,
        "image_metrics": _image_metrics(workbench, query, total),
        "images": rows,
        "pagination": {
            "page": safe_page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "query": query,
            "filter": filter_by,
        },
    }


def _sqlite_image_row(row: dict[str, str]) -> dict[str, str]:
    manual_status = row.get("manual_status", "")
    image_url = row.get("image_url", "")
    return {
        "review_id": "",
        "outward_code": row.get("outward_code", ""),
        "result_filename": Path(unquote(urlparse(image_url).path)).name,
        "image_url": image_url,
        "download_status": row.get("download_status", ""),
        "model_status": row.get("model_status", ""),
        "manual_status": manual_status or "未标注",
        "image_src": image_url,
    }


def _product_payload(workbench: ReviewWorkbench, outward_code: str | None = None, blocking: bool = True) -> dict:
    state = workbench.state_snapshot(blocking=blocking)
    if state is None:
        return {"loading": True, "metrics": _empty_metrics(), "product": None, "images": [], "raw_images": []}
    product = workbench.product_by_code(outward_code, state) if outward_code else workbench.next_unfinished_product(state)
    final_urls = {item.image_url for item in product.images if item.image_url} if product else set()
    return {
        "metrics": state.metrics,
        "product": _product_meta_payload(product, state.invalid_product_codes) if product else None,
        "images": [_image_payload(item) for item in product.images] if product else [],
        "raw_images": [_image_payload(item, final_urls) for item in product.raw_images] if product else [],
    }


def _empty_metrics() -> dict[str, int]:
    return {"total_products": 0, "completed_products": 0, "invalid_products": 0, "pending_annotation_products": 0, "unfinished_products": 0}


def _empty_image_metrics() -> dict[str, int]:
    return {"total_images": 0, "model_final_images": 0, "qualified_images": 0}


def _image_metrics(workbench: ReviewWorkbench, outward_code: str, fallback_total: int) -> dict[str, int]:
    total_images = fallback_total
    qualified_images = 0
    if workbench.state_db:
        total_images = workbench.state_db.count_product_images(outward_code) or fallback_total
        qualified_images = workbench.state_db.count_review_status("合格", outward_code)
        model_final_images = workbench.state_db.count_model_status("模型选中", outward_code)
    else:
        model_final_images = _model_final_image_count(workbench, outward_code)
    return {
        "total_images": total_images,
        "model_final_images": model_final_images,
        "qualified_images": qualified_images,
    }


def _filter_image_rows(rows: list[dict[str, str]], filter_by: str) -> list[dict[str, str]]:
    if filter_by == "model_final":
        return [row for row in rows if row["model_status"] == "模型选中"]
    if filter_by == "qualified":
        return [row for row in rows if row["manual_status"] == "合格"]
    return rows


def _model_final_image_count(workbench: ReviewWorkbench, outward_code: str) -> int:
    if not workbench.result_root.exists():
        return 0
    product_dirs = [workbench.result_root / outward_code] if outward_code else [path for path in workbench.result_root.iterdir() if path.is_dir()]
    total = 0
    for product_dir in product_dirs:
        if product_dir.exists():
            total += sum(1 for status in model_statuses(product_dir).values() if status == "模型选中")
    return total


def _current_product(products: list[ReviewProduct], batch: list[ReviewImage]) -> ReviewProduct | None:
    if not batch:
        return None
    outward_code = batch[0].outward_code
    return next((product for product in products if product.outward_code == outward_code), None)


def _product_meta_payload(product: ReviewProduct, invalid_product_codes: set[str]) -> dict:
    return {"outward_code": product.outward_code, "status": product_status(product, invalid_product_codes)}


def _image_payload(item: ReviewImage, final_urls: set[str] | None = None) -> dict:
    return {
        "review_id": item.review_id,
        "outward_code": item.outward_code,
        "result_filename": item.result_filename,
        "image_url": item.image_url,
        "review_status": item.review_status,
        "in_final_result": bool(final_urls and item.image_url in final_urls),
        "image_src": f"/image/{item.review_id}",
    }


def _all_image_rows(workbench: ReviewWorkbench, state) -> list[dict[str, str]]:
    rows = []
    live_statuses = _live_review_statuses(workbench, state)
    for product in state.products:
        product_dir = workbench.result_root / product.outward_code
        manifests = raw_manifest_rows(product_dir)
        model_by_source = model_statuses(product_dir)
        for item in product.raw_images:
            manifest = manifests.get(item.result_filename, {})
            manual_status = live_statuses.get((item.outward_code, item.image_url), item.review_status)
            rows.append({
                "review_id": item.review_id,
                "outward_code": item.outward_code,
                "result_filename": item.result_filename,
                "image_url": item.image_url,
                "download_status": manifest.get("status", ""),
                "model_status": model_by_source.get(item.result_filename, "未处理"),
                "manual_status": manual_status or "未标注",
                "image_src": f"/image/{item.review_id}",
            })
    rows.sort(key=lambda row: (row["outward_code"], row["result_filename"]))
    return rows


def _live_review_statuses(workbench: ReviewWorkbench, state) -> dict[tuple[str, str], str]:
    if not workbench.state_db:
        return {}
    keys = {
        (image.outward_code, image.image_url)
        for product in state.products
        for image in product.raw_images
        if image.image_url
    }
    return workbench.state_db.read_review_statuses(keys)


def _int_param(params: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(params.get(key, [str(default)])[0])
    except (TypeError, ValueError):
        return default


_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品标注审核工作台</title><style>
:root{--blue-700:#1d4ed8;--blue-600:#2563eb;--blue-100:#dbeafe;--blue-50:#eff6ff;--slate-900:#0f172a;--slate-700:#334155;--slate-500:#64748b;--slate-200:#e2e8f0;--slate-100:#f1f5f9;--surface:#fff;--danger:#dc2626;--success:#16a34a;--warning:#d97706}
*{box-sizing:border-box}body{margin:0;padding-bottom:104px;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;background:#eef4fb;color:var(--slate-900);font-size:14px;line-height:1.5}button,input{font:inherit}button{min-height:44px;border:1px solid var(--blue-600);background:var(--blue-600);color:white;border-radius:6px;padding:10px 16px;cursor:pointer;transition:background .18s ease,border-color .18s ease,box-shadow .18s ease}button:hover{background:var(--blue-700);border-color:var(--blue-700)}button:disabled{cursor:not-allowed;opacity:.5}button:focus-visible,input:focus-visible{outline:3px solid rgba(37,99,235,.28);outline-offset:2px}input{min-height:44px;border:1px solid #cbd5e1;border-radius:6px;padding:10px 12px;background:white;color:var(--slate-900)}
.top{position:sticky;top:0;background:rgba(255,255,255,.98);border-bottom:1px solid var(--slate-200);padding:14px 20px;z-index:2;box-shadow:0 8px 28px rgba(15,23,42,.06)}.appnav{display:grid;grid-template-columns:minmax(240px,1fr) auto minmax(160px,1fr);align-items:center;gap:18px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--slate-200)}.brand{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:700;color:var(--slate-900);white-space:nowrap}.brandMark{display:inline-grid;place-items:center;width:32px;height:32px;border-radius:8px;background:var(--blue-600);color:white;font-size:14px}.navmeta{justify-self:end;color:var(--slate-500);font-size:13px;white-space:nowrap}.menu{display:inline-flex;justify-self:center;gap:4px;margin:0;padding:4px;background:#f8fbff;border:1px solid var(--slate-200);border-radius:10px}.menu button{min-height:36px;padding:7px 14px;border-radius:7px}.menu button,button.secondary{background:transparent;color:var(--slate-700);border-color:transparent}.menu button:hover{background:white;color:var(--blue-700);border-color:#bfdbfe}.menu button.active{background:var(--blue-600);color:white;border-color:var(--blue-600);box-shadow:0 6px 16px rgba(37,99,235,.22)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:12px}.metric{border:1px solid var(--slate-200);background:linear-gradient(180deg,#fff,#f8fbff);border-radius:8px;padding:12px 14px;color:var(--slate-500)}.metric b{display:block;margin-top:2px;font-size:26px;line-height:1.1;color:var(--slate-900);font-variant-numeric:tabular-nums}.metricButton{min-height:auto;text-align:left}.metricButton:hover,.metricButton.active{background:var(--blue-50);border-color:var(--blue-600);box-shadow:0 0 0 2px rgba(37,99,235,.12)}.labelStats{display:inline-flex;gap:0;flex-wrap:wrap;margin-top:10px;border:1px solid var(--slate-200);background:white;border-radius:8px;overflow:hidden;box-shadow:0 4px 12px rgba(15,23,42,.04)}.statPill{display:inline-flex;align-items:center;gap:8px;padding:7px 12px;color:var(--slate-500);border-right:1px solid var(--slate-200);line-height:1}.statPill:last-child{border-right:0}.statPill b{display:inline-grid;place-items:center;min-width:28px;height:22px;border-radius:999px;background:var(--blue-50);color:var(--blue-700);font-size:14px;font-variant-numeric:tabular-nums}#imageMetrics{margin-top:14px}#imageTools{margin-top:18px}.productbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}#productHeader{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:14px;padding:10px 12px;border:1px solid var(--slate-200);background:linear-gradient(180deg,#fff,#f8fbff);border-radius:8px;box-shadow:0 4px 12px rgba(15,23,42,.04)}.product{font-size:16px;font-weight:700;color:var(--slate-900)}#productHeader .product{font-size:18px;font-weight:800;line-height:1.2}.status{display:inline-flex;align-items:center;border:1px solid #bfdbfe;border-radius:999px;padding:4px 10px;background:var(--blue-50);color:#1e40af}#productHeader .status{font-weight:700;padding:5px 11px;background:#eff6ff;border-color:#bfdbfe}.hint{color:var(--slate-500);font-size:13px}
.actions{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:10px;align-items:center;flex-wrap:wrap;max-width:calc(100vw - 32px);background:white;border:1px solid var(--slate-200);border-radius:8px;padding:10px 12px;box-shadow:0 18px 45px rgba(15,23,42,.18);z-index:3}.tabsbar{display:flex;gap:4px;align-items:flex-end;margin-top:12px;border-bottom:1px solid var(--slate-200)}.tab{background:transparent;color:var(--slate-500);border:1px solid transparent;border-bottom:0;border-radius:6px 6px 0 0;padding:10px 16px;margin-bottom:-1px}.tab.active{background:white;color:var(--blue-700);border-color:var(--slate-200);box-shadow:0 -2px 0 white inset}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:16px;padding:18px}.card{background:white;border:1px solid var(--slate-200);border-radius:8px;overflow:hidden;box-shadow:0 10px 24px rgba(15,23,42,.06);transition:border-color .18s ease,box-shadow .18s ease}.card:hover{box-shadow:0 14px 32px rgba(15,23,42,.1)}.card.selected{border-color:var(--danger);background:#fff7f7}.card.valid{border-color:var(--success);background:#f0fdf4}.card.pending-valid{border-color:var(--warning);background:#fffbeb}.card img{display:block;width:100%;height:210px;object-fit:contain;background:#f8fafc}.review-image{cursor:pointer}.meta{padding:12px;font-size:12px;line-height:1.5;word-break:break-all;color:var(--slate-700)}.flag{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:14px;color:var(--slate-900)}.flag input{min-height:auto}.empty{padding:40px;color:var(--slate-500)}.tablewrap{padding:18px;overflow:auto}table{border-collapse:separate;border-spacing:0;width:100%;background:white;border:1px solid var(--slate-200);border-radius:8px;overflow:hidden;box-shadow:0 10px 24px rgba(15,23,42,.06)}th,td{border-bottom:1px solid var(--slate-200);padding:12px;text-align:left;white-space:nowrap}th{background:#f8fbff;color:var(--slate-700);font-weight:700}tr:last-child td{border-bottom:0}
@media(max-width:860px){.appnav{grid-template-columns:1fr;align-items:start}.menu{justify-self:start}.navmeta{justify-self:start}}@media(max-width:640px){.top{padding:12px}.appnav{gap:10px}.menu{width:100%;display:grid;grid-template-columns:repeat(3,1fr)}.menu button{padding:7px 8px}.productbar,.actions{gap:8px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;padding:12px}.card img{height:170px}.actions{left:12px;right:12px;transform:none;bottom:12px}.actions button{flex:1}}
</style></head><body><div class="top"><div class="appnav"><div class="brand"><span class="brandMark">审</span><span>商品标注审核工作台</span></div><nav class="menu" aria-label="主导航"><button id="menuLabel" class="active">商品标注</button><button id="menuStats">商品统计</button><button id="menuImages">全部图片</button></nav><div class="navmeta">本地审核 · 状态可追溯</div></div><div id="mainMetrics" class="metrics">
<div class="metric">总商品数<b id="total">0</b></div><div class="metric">完成商品数<b id="done">0</b></div><div class="metric">无效商品数<b id="invalid">0</b></div><div class="metric">待标注商品数<b id="pendingAnnotation">0</b></div><div class="metric">未完成<b id="pending">0</b></div></div><div id="labelTools"><div id="productHeader" class="productbar"><span class="product" id="productCode">无当前商品</span><span class="status" id="productStatus">-</span></div><div class="tabsbar" role="tablist" aria-label="商品图片视图"><button class="tab active" role="tab" aria-selected="true" data-view="final">模型最终结果</button><button class="tab" role="tab" aria-selected="false" data-view="original">商品原始照片</button></div><div id="finalStats" class="labelStats"><span class="statPill">全部图片数 <b id="finalImageCount">0</b></span><span class="statPill">勾选不合格数 <b id="invalidCheckedCount">0</b></span></div><div id="rawStats" class="labelStats" style="display:none"><span class="statPill">全部图片数 <b id="rawImageCount">0</b></span><span class="statPill">合格待确认 <b id="rawPendingConfirmCount">0</b></span></div><div class="actions"><button id="submit">提交本商品</button><button class="secondary" id="reload">刷新</button><span class="hint" id="msg"></span></div></div><div id="statsTools" style="display:none" class="productbar"><input id="statsSearch" placeholder="输入商品编码搜索"><button id="searchStats">搜索</button><button id="prevPage" class="secondary">上一页</button><span class="hint" id="pageInfo">每页50个商品</span><button id="nextPage" class="secondary">下一页</button></div><div id="imageMetrics" style="display:none" class="metrics"><button class="metric metricButton" id="allImageMetric" onclick="setImageFilter('')" title="显示全部图片">全部图片数<b id="allImageCount">0</b></button><button class="metric metricButton" id="modelFinalImageMetric" onclick="setImageFilter('model_final')" title="只显示模型最终结果图片">模型最终结果数<b id="modelFinalImageCount">0</b></button><button class="metric metricButton" id="qualifiedImageMetric" onclick="setImageFilter('qualified')" title="只显示人工标注合格图片">合格数<b id="qualifiedImageCount">0</b></button></div><div id="imageTools" style="display:none" class="productbar"><input id="imageSearch" placeholder="精准编码搜索"><button id="searchImages">搜索</button><button id="prevImagePage" class="secondary">上一页</button><span class="hint" id="imagePageInfo">每页100张图片</span><button id="nextImagePage" class="secondary">下一页</button></div></div><main id="grid" class="grid"></main><script>
let images=[],rawImages=[],product=null,activeView='final',activeMenu='label',statsPage=1,statsTotalPages=1,statsQuery='',imagePage=1,imageTotalPages=1,imageQuery='',imageFilter='',rawAdjustments={};
async function loadProduct(code){const url=code?`/api/product?outward_code=${encodeURIComponent(code)}`:'/api/product';const r=await fetch(url);const d=await r.json();if(d.loading){setMenu('label');renderMetrics(d.metrics||{});productCode.textContent='数据加载中';productStatus.textContent='-';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadProduct(code),2000);return;}images=d.images||[];rawImages=d.raw_images||[];product=d.product||null;rawAdjustments={};setMenu('label');renderMetrics(d.metrics||{});renderLabel();}
async function loadStats(page=1){statsQuery=statsSearch.value.trim();const r=await fetch(`/api/products?page=${page}&page_size=50&q=${encodeURIComponent(statsQuery)}`);const d=await r.json();if(d.loading){setMenu('stats');renderMetrics(d.metrics||{});pageInfo.textContent='数据加载中，每页50个商品';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadStats(page),2000);return;}setMenu('stats');renderMetrics(d.metrics||{});renderStats(d.products||[],d.pagination||{});}async function loadImages(page=1){imageQuery=imageSearch.value.trim();const r=await fetch(`/api/images?page=${page}&page_size=100&q=${encodeURIComponent(imageQuery)}&filter=${encodeURIComponent(imageFilter)}`);const d=await r.json();if(d.loading){setMenu('images');renderMetrics(d.metrics||{});renderImageMetrics(d.image_metrics||{});imagePageInfo.textContent='数据加载中，每页100张图片';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadImages(page),2000);return;}setMenu('images');renderMetrics(d.metrics||{});renderImageMetrics(d.image_metrics||{});renderAllImages(d.images||[],d.pagination||{});}
function setMenu(name){activeMenu=name;menuLabel.classList.toggle('active',name==='label');menuStats.classList.toggle('active',name==='stats');menuImages.classList.toggle('active',name==='images');mainMetrics.style.display=name==='images'?'none':'flex';labelTools.style.display=name==='label'?'block':'none';statsTools.style.display=name==='stats'?'flex':'none';imageTools.style.display=name==='images'?'flex':'none';imageMetrics.style.display=name==='images'?'flex':'none';}
function renderMetrics(m){total.textContent=m.total_products||0;done.textContent=m.completed_products||0;invalid.textContent=m.invalid_products||0;pendingAnnotation.textContent=m.pending_annotation_products||0;pending.textContent=m.unfinished_products||0;}
function renderImageMetrics(m){allImageCount.textContent=m.total_images||0;modelFinalImageCount.textContent=m.model_final_images||0;qualifiedImageCount.textContent=m.qualified_images||0;allImageMetric.classList.toggle('active',!imageFilter);modelFinalImageMetric.classList.toggle('active',imageFilter==='model_final');qualifiedImageMetric.classList.toggle('active',imageFilter==='qualified');}
function setImageFilter(value){imageFilter=imageFilter===value?'':value;loadImages(1);}
function renderLabel(){productCode.textContent=product?product.outward_code:'无当前商品';productStatus.textContent=product?product.status:'-';renderGrid();}
function renderGrid(){grid.innerHTML='';document.querySelectorAll('.tab').forEach(x=>{const active=x.dataset.view===activeView;x.classList.toggle('active',active);x.setAttribute('aria-selected',active?'true':'false');});finalStats.style.display=activeView==='final'?'inline-flex':'none';rawStats.style.display=activeView==='original'?'inline-flex':'none';updateActionButton();if(activeView==='original'){renderOriginalImages();return;}renderFinalImages();}
function renderFinalImages(){if(!images.length){grid.className='empty';grid.textContent='暂无待标注商品';updateFinalStats();return;}grid.className='grid';for(const it of images){const el=document.createElement('section');el.className='card';el.innerHTML=`<img class="review-image" src="${it.image_src}" loading="lazy" onclick="toggleInvalid(this)"><div class="meta"><div>${it.outward_code}</div><div>${it.result_filename}</div><div class="flag">状态 <span class="statusText">合格</span></div><label class="flag"><input type="checkbox" data-id="${it.review_id}" onchange="syncCard(this)"> 不符合要求</label></div>`;const checkbox=el.querySelector('input[data-id]');checkbox.checked=it.review_status==='不合格';syncCard(checkbox);grid.appendChild(el);}updateFinalStats();}
function renderOriginalImages(){if(!rawImages.length){grid.className='empty';grid.textContent='没有商品原始照片';updateRawStats();return;}grid.className='grid';for(const it of rawImages){const status=displayStatus(it);const el=document.createElement('section');el.className='card raw-card';el.dataset.id=it.review_id;el.dataset.originalStatus=status;el.dataset.status=status;el.innerHTML=`<img class="review-image" src="${it.image_src}" loading="lazy" onclick="toggleRawStatus(this)"><div class="meta"><div>${it.outward_code}</div><div>${it.result_filename}</div><div class="flag">状态 <span class="statusText">${status}</span></div></div>`;setRawCardStatus(el,status,false);grid.appendChild(el);}updateRawStats();}
function renderStats(rows,pagination={}){statsPage=pagination.page||1;statsTotalPages=pagination.total_pages||1;pageInfo.textContent=`第 ${statsPage}/${statsTotalPages} 页，共 ${pagination.total||0} 个商品，每页50个商品`;prevPage.disabled=statsPage<=1;nextPage.disabled=statsPage>=statsTotalPages;grid.className='tablewrap';grid.innerHTML='<table><thead><tr><th>商品编码</th><th>商品图片</th><th>建模图片数</th><th>原始抠图数</th><th>模型筛选最终结果</th><th>人工标注数</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table>';const body=grid.querySelector('tbody');for(const row of rows){const tr=document.createElement('tr');const productImage=row.standard_image_url?`<img src="${row.standard_image_url}" loading="lazy" style="width:72px;height:72px;object-fit:contain;background:#eef1f5">`:'-';tr.innerHTML=`<td>${row.outward_code}</td><td>${productImage}</td><td>${row.standard_count}</td><td>${row.cutout_count}</td><td>${row.final_count}</td><td>${row.manual_count}</td><td>${row.status}</td><td><button onclick="loadProduct('${row.outward_code}')">去标注</button></td>`;body.appendChild(tr);}}function renderAllImages(rows,pagination={}){imagePage=pagination.page||1;imageTotalPages=pagination.total_pages||1;imagePageInfo.textContent=`第 ${imagePage}/${imageTotalPages} 页，共 ${pagination.total||0} 张图片，每页100张图片`;prevImagePage.disabled=imagePage<=1;nextImagePage.disabled=imagePage>=imageTotalPages;grid.className='tablewrap';grid.innerHTML='<table><thead><tr><th>编码</th><th>图片</th><th>URL</th><th>下载状态</th><th>模型处理状态</th><th>人工标注状态</th></tr></thead><tbody></tbody></table>';const body=grid.querySelector('tbody');for(const row of rows){const tr=document.createElement('tr');tr.innerHTML=`<td>${row.outward_code}</td><td><img src="${row.image_src}" loading="lazy" style="width:72px;height:72px;object-fit:contain;background:#eef1f5"></td><td>${row.image_url}</td><td>${row.download_status}</td><td>${row.model_status}</td><td>${row.manual_status}</td>`;body.appendChild(tr);}}
function displayStatus(item){return item.review_status||(item.in_final_result?'合格待确认':'未标注');}
function nextRawStatus(status){return status==='未标注'||status==='不合格'||status==='合格待确认'?'合格':'不合格';}
function toggleInvalid(img){const card=img.closest('.card');const checkbox=card.querySelector('input[data-id]');checkbox.checked=!checkbox.checked;syncCard(checkbox);}
function toggleRawStatus(img){const card=img.closest('.card');setRawCardStatus(card,nextRawStatus(card.dataset.status||'未标注'),true);}
function setRawCardStatus(card,status,track){card.dataset.status=status;card.classList.toggle('selected',status==='不合格');card.classList.toggle('valid',status==='合格');card.classList.toggle('pending-valid',status==='合格待确认');card.querySelector('.statusText').textContent=status;if(track){if(status===(card.dataset.originalStatus||'未标注'))delete rawAdjustments[card.dataset.id];else rawAdjustments[card.dataset.id]=status;}updateActionButton();}
function syncCard(checkbox){const card=checkbox.closest('.card');card.classList.toggle('selected',checkbox.checked);card.querySelector('.statusText').textContent=checkbox.checked?'不合格':'合格';updateFinalStats();}
function updateFinalStats(){finalImageCount.textContent=images.length;invalidCheckedCount.textContent=document.querySelectorAll('input[data-id]:checked').length;}
function updateRawStats(){rawImageCount.textContent=rawImages.length;rawPendingConfirmCount.textContent=document.querySelectorAll('.raw-card.pending-valid').length;}
function updateActionButton(){const rawCount=Object.keys(rawAdjustments).length;if(activeView==='original'){submit.textContent=`提交调整 ${rawCount} 张`;submit.disabled=rawCount===0;updateRawStats();return;}submit.textContent='提交本商品';submit.disabled=!images.length;updateFinalStats();}
async function submitBatch(){if(activeView==='original'){await submitRawAdjustments();return;}if(!images.length)return;const statuses={};document.querySelectorAll('input[data-id]').forEach(x=>{statuses[x.dataset.id]=x.checked?'不合格':'合格';});msg.textContent='提交中...';const r=await fetch('/api/product/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({statuses})});const d=await r.json();images=d.images||[];rawImages=d.raw_images||[];product=d.product||null;rawAdjustments={};msg.textContent=`已更新 ${d.updated||0} 张`;renderMetrics(d.metrics||{});renderLabel();}
async function submitRawAdjustments(){const statuses={...rawAdjustments};if(!Object.keys(statuses).length)return;msg.textContent='提交中...';const r=await fetch('/api/product/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({statuses,outward_code:product&&product.outward_code})});const d=await r.json();images=d.images||images;rawImages=d.raw_images||rawImages;product=d.product||product;rawAdjustments={};msg.textContent=`已更新 ${d.updated||0} 张`;renderMetrics(d.metrics||{});renderLabel();}
document.querySelectorAll('.tab').forEach(x=>x.onclick=()=>{activeView=x.dataset.view;msg.textContent='';renderGrid();});menuLabel.onclick=()=>loadProduct();menuStats.onclick=()=>loadStats(1);menuImages.onclick=()=>loadImages(1);searchStats.onclick=()=>loadStats(1);statsSearch.onkeydown=e=>{if(e.key==='Enter')loadStats(1);};searchImages.onclick=()=>loadImages(1);imageSearch.onkeydown=e=>{if(e.key==='Enter')loadImages(1);};prevPage.onclick=()=>loadStats(statsPage-1);nextPage.onclick=()=>loadStats(statsPage+1);prevImagePage.onclick=()=>loadImages(imagePage-1);nextImagePage.onclick=()=>loadImages(imagePage+1);submit.onclick=submitBatch;reload.onclick=()=>activeMenu==='stats'?loadStats(statsPage):(activeMenu==='images'?loadImages(imagePage):loadProduct(product&&product.outward_code));const initialCode=new URLSearchParams(location.search).get('outward_code');loadProduct(initialCode);
</script></body></html>"""
