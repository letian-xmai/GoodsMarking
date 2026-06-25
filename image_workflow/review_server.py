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


def _images_payload(workbench: ReviewWorkbench, page: int = 1, page_size: int = 100, query: str = "", blocking: bool = True) -> dict:
    clean_query = str(query or "").strip()
    safe_page_size = max(1, page_size)
    state = workbench.state_snapshot(blocking=blocking)
    if state is None:
        return {
            "loading": True,
            "metrics": _empty_metrics(),
            "images": [],
            "pagination": {"page": 1, "page_size": safe_page_size, "total": 0, "total_pages": 1, "query": clean_query},
        }
    rows = _all_image_rows(workbench, state)
    if clean_query:
        rows = [row for row in rows if row["outward_code"] == clean_query]
    total = len(rows)
    total_pages = max(1, (total + safe_page_size - 1) // safe_page_size)
    safe_page = min(max(1, page), total_pages)
    start = (safe_page - 1) * safe_page_size
    return {
        "metrics": state.metrics,
        "images": rows[start:start + safe_page_size],
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
            "query": clean_query,
        },
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
    for product in state.products:
        product_dir = workbench.result_root / product.outward_code
        manifests = raw_manifest_rows(product_dir)
        model_by_source = model_statuses(product_dir)
        for item in product.raw_images:
            manifest = manifests.get(item.result_filename, {})
            rows.append({
                "review_id": item.review_id,
                "outward_code": item.outward_code,
                "result_filename": item.result_filename,
                "image_url": item.image_url,
                "download_status": manifest.get("status", ""),
                "model_status": model_by_source.get(item.result_filename, "未处理"),
                "manual_status": item.review_status or "未标注",
                "image_src": f"/image/{item.review_id}",
            })
    rows.sort(key=lambda row: (row["outward_code"], row["result_filename"]))
    return rows


def _int_param(params: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(params.get(key, [str(default)])[0])
    except (TypeError, ValueError):
        return default


_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品标注审核工作台</title><style>
body{margin:0;padding-bottom:92px;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;background:#f6f7f9;color:#1f2937}.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #d9dee7;padding:14px 18px;z-index:2}.menu{display:flex;gap:8px;margin-bottom:12px}.metrics{display:flex;gap:12px;flex-wrap:wrap}.metric{border:1px solid #d9dee7;background:#fbfcfe;border-radius:6px;padding:10px 14px;min-width:120px}.metric b{display:block;font-size:24px}.productbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}.actions{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px;background:#fff;border:1px solid #d9dee7;border-radius:6px;padding:10px 12px;box-shadow:0 8px 24px rgba(15,23,42,.14);z-index:3}.tabsbar{display:flex;gap:0;align-items:flex-end;margin-top:10px;border-bottom:1px solid #d9dee7}button{border:1px solid #2563eb;background:#2563eb;color:white;border-radius:6px;padding:9px 14px;font-size:14px;cursor:pointer}button.secondary,.menu button{background:white;color:#1f2937;border-color:#c6ccd6}.menu button.active{background:#2563eb;color:white;border-color:#2563eb}.tab{background:transparent;color:#475467;border:1px solid transparent;border-bottom:0;border-radius:6px 6px 0 0;padding:10px 16px;margin-bottom:-1px}.tab.active{background:#fff;color:#1f2937;border-color:#d9dee7;box-shadow:0 -1px 0 #fff inset}.product{font-weight:600}.status{border:1px solid #c6ccd6;border-radius:999px;padding:4px 10px;background:#fbfcfe}.hint{color:#667085;font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;padding:16px}.card{background:white;border:1px solid #d9dee7;border-radius:6px;overflow:hidden}.card.selected{border-color:#dc2626;background:#fff7f7}.card.valid{border-color:#16a34a;background:#f0fdf4}.card.pending-valid{border-color:#d97706;background:#fffbeb}.card img{display:block;width:100%;height:190px;object-fit:contain;background:#eef1f5}.review-image{cursor:pointer}.meta{padding:10px;font-size:12px;line-height:1.45;word-break:break-all}.flag{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:14px}.empty{padding:32px;color:#667085}.tablewrap{padding:16px;overflow:auto}table{border-collapse:collapse;width:100%;background:white;border:1px solid #d9dee7}th,td{border-bottom:1px solid #e5e9f0;padding:10px;text-align:left;white-space:nowrap}th{background:#f1f4f8;font-weight:600}</style></head><body><div class="top"><nav class="menu"><button id="menuLabel" class="active">商品标注</button><button id="menuStats">商品统计</button><button id="menuImages">全部图片</button></nav><div class="metrics">
<div class="metric">总商品数<b id="total">0</b></div><div class="metric">完成商品数<b id="done">0</b></div><div class="metric">无效商品数<b id="invalid">0</b></div><div class="metric">待标注商品数<b id="pendingAnnotation">0</b></div><div class="metric">未完成<b id="pending">0</b></div></div><div id="labelTools"><div class="productbar"><span class="product" id="productCode">无当前商品</span><span class="status" id="productStatus">-</span></div><div class="tabsbar" role="tablist" aria-label="商品图片视图"><button class="tab active" role="tab" aria-selected="true" data-view="final">模型最终结果</button><button class="tab" role="tab" aria-selected="false" data-view="original">商品原始照片</button></div><div class="actions"><button id="submit">提交本商品</button><button class="secondary" id="reload">刷新</button><span class="hint" id="msg"></span></div></div><div id="statsTools" style="display:none" class="productbar"><input id="statsSearch" placeholder="输入商品编码搜索"><button id="searchStats">搜索</button><button id="prevPage" class="secondary">上一页</button><span class="hint" id="pageInfo">每页50个商品</span><button id="nextPage" class="secondary">下一页</button></div><div id="imageTools" style="display:none" class="productbar"><input id="imageSearch" placeholder="精准编码搜索"><button id="searchImages">搜索</button><button id="prevImagePage" class="secondary">上一页</button><span class="hint" id="imagePageInfo">每页100张图片</span><button id="nextImagePage" class="secondary">下一页</button></div></div><main id="grid" class="grid"></main><script>
let images=[],rawImages=[],product=null,activeView='final',activeMenu='label',statsPage=1,statsTotalPages=1,statsQuery='',imagePage=1,imageTotalPages=1,imageQuery='',rawAdjustments={};
async function loadProduct(code){const url=code?`/api/product?outward_code=${encodeURIComponent(code)}`:'/api/product';const r=await fetch(url);const d=await r.json();if(d.loading){setMenu('label');renderMetrics(d.metrics||{});productCode.textContent='数据加载中';productStatus.textContent='-';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadProduct(code),2000);return;}images=d.images||[];rawImages=d.raw_images||[];product=d.product||null;rawAdjustments={};setMenu('label');renderMetrics(d.metrics||{});renderLabel();}
async function loadStats(page=1){statsQuery=statsSearch.value.trim();const r=await fetch(`/api/products?page=${page}&page_size=50&q=${encodeURIComponent(statsQuery)}`);const d=await r.json();if(d.loading){setMenu('stats');renderMetrics(d.metrics||{});pageInfo.textContent='数据加载中，每页50个商品';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadStats(page),2000);return;}setMenu('stats');renderMetrics(d.metrics||{});renderStats(d.products||[],d.pagination||{});}async function loadImages(page=1){imageQuery=imageSearch.value.trim();const r=await fetch(`/api/images?page=${page}&page_size=100&q=${encodeURIComponent(imageQuery)}`);const d=await r.json();if(d.loading){setMenu('images');renderMetrics(d.metrics||{});imagePageInfo.textContent='数据加载中，每页100张图片';grid.className='empty';grid.textContent='数据加载中';msg.textContent='数据加载中';setTimeout(()=>loadImages(page),2000);return;}setMenu('images');renderMetrics(d.metrics||{});renderAllImages(d.images||[],d.pagination||{});}
function setMenu(name){activeMenu=name;menuLabel.classList.toggle('active',name==='label');menuStats.classList.toggle('active',name==='stats');menuImages.classList.toggle('active',name==='images');labelTools.style.display=name==='label'?'block':'none';statsTools.style.display=name==='stats'?'flex':'none';imageTools.style.display=name==='images'?'flex':'none';}
function renderMetrics(m){total.textContent=m.total_products||0;done.textContent=m.completed_products||0;invalid.textContent=m.invalid_products||0;pendingAnnotation.textContent=m.pending_annotation_products||0;pending.textContent=m.unfinished_products||0;}
function renderLabel(){productCode.textContent=product?product.outward_code:'无当前商品';productStatus.textContent=product?product.status:'-';renderGrid();}
function renderGrid(){grid.innerHTML='';document.querySelectorAll('.tab').forEach(x=>{const active=x.dataset.view===activeView;x.classList.toggle('active',active);x.setAttribute('aria-selected',active?'true':'false');});updateActionButton();if(activeView==='original'){renderOriginalImages();return;}renderFinalImages();}
function renderFinalImages(){if(!images.length){grid.className='empty';grid.textContent='暂无待标注商品';return;}grid.className='grid';for(const it of images){const el=document.createElement('section');el.className='card';el.innerHTML=`<img class="review-image" src="${it.image_src}" loading="lazy" onclick="toggleInvalid(this)"><div class="meta"><div>${it.outward_code}</div><div>${it.result_filename}</div><div class="flag">状态 <span class="statusText">合格</span></div><label class="flag"><input type="checkbox" data-id="${it.review_id}" onchange="syncCard(this)"> 不符合要求</label></div>`;const checkbox=el.querySelector('input[data-id]');checkbox.checked=it.review_status==='不合格';syncCard(checkbox);grid.appendChild(el);}}
function renderOriginalImages(){if(!rawImages.length){grid.className='empty';grid.textContent='没有商品原始照片';return;}grid.className='grid';for(const it of rawImages){const status=displayStatus(it);const el=document.createElement('section');el.className='card raw-card';el.dataset.id=it.review_id;el.dataset.originalStatus=status;el.dataset.status=status;el.innerHTML=`<img class="review-image" src="${it.image_src}" loading="lazy" onclick="toggleRawStatus(this)"><div class="meta"><div>${it.outward_code}</div><div>${it.result_filename}</div><div class="flag">状态 <span class="statusText">${status}</span></div></div>`;setRawCardStatus(el,status,false);grid.appendChild(el);}}
function renderStats(rows,pagination={}){statsPage=pagination.page||1;statsTotalPages=pagination.total_pages||1;pageInfo.textContent=`第 ${statsPage}/${statsTotalPages} 页，共 ${pagination.total||0} 个商品，每页50个商品`;prevPage.disabled=statsPage<=1;nextPage.disabled=statsPage>=statsTotalPages;grid.className='tablewrap';grid.innerHTML='<table><thead><tr><th>商品编码</th><th>建模图片数</th><th>原始抠图数</th><th>模型筛选最终结果</th><th>人工标注数</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table>';const body=grid.querySelector('tbody');for(const row of rows){const tr=document.createElement('tr');tr.innerHTML=`<td>${row.outward_code}</td><td>${row.standard_count}</td><td>${row.cutout_count}</td><td>${row.final_count}</td><td>${row.manual_count}</td><td>${row.status}</td><td><button onclick="loadProduct('${row.outward_code}')">去标注</button></td>`;body.appendChild(tr);}}function renderAllImages(rows,pagination={}){imagePage=pagination.page||1;imageTotalPages=pagination.total_pages||1;imagePageInfo.textContent=`第 ${imagePage}/${imageTotalPages} 页，共 ${pagination.total||0} 张图片，每页100张图片`;prevImagePage.disabled=imagePage<=1;nextImagePage.disabled=imagePage>=imageTotalPages;grid.className='tablewrap';grid.innerHTML='<table><thead><tr><th>编码</th><th>图片</th><th>URL</th><th>下载状态</th><th>模型处理状态</th><th>人工标注状态</th></tr></thead><tbody></tbody></table>';const body=grid.querySelector('tbody');for(const row of rows){const tr=document.createElement('tr');tr.innerHTML=`<td>${row.outward_code}</td><td><img src="${row.image_src}" loading="lazy" style="width:72px;height:72px;object-fit:contain;background:#eef1f5"></td><td>${row.image_url}</td><td>${row.download_status}</td><td>${row.model_status}</td><td>${row.manual_status}</td>`;body.appendChild(tr);}}
function displayStatus(item){return item.review_status||(item.in_final_result?'合格待确认':'未标注');}
function nextRawStatus(status){return status==='未标注'||status==='不合格'||status==='合格待确认'?'合格':'不合格';}
function toggleInvalid(img){const card=img.closest('.card');const checkbox=card.querySelector('input[data-id]');checkbox.checked=!checkbox.checked;syncCard(checkbox);}
function toggleRawStatus(img){const card=img.closest('.card');setRawCardStatus(card,nextRawStatus(card.dataset.status||'未标注'),true);}
function setRawCardStatus(card,status,track){card.dataset.status=status;card.classList.toggle('selected',status==='不合格');card.classList.toggle('valid',status==='合格');card.classList.toggle('pending-valid',status==='合格待确认');card.querySelector('.statusText').textContent=status;if(track){if(status===(card.dataset.originalStatus||'未标注'))delete rawAdjustments[card.dataset.id];else rawAdjustments[card.dataset.id]=status;}updateActionButton();}
function syncCard(checkbox){const card=checkbox.closest('.card');card.classList.toggle('selected',checkbox.checked);card.querySelector('.statusText').textContent=checkbox.checked?'不合格':'合格';}
function updateActionButton(){const rawCount=Object.keys(rawAdjustments).length;if(activeView==='original'){submit.textContent=`提交调整 ${rawCount} 张`;submit.disabled=rawCount===0;return;}submit.textContent='提交本商品';submit.disabled=!images.length;}
async function submitBatch(){if(activeView==='original'){await submitRawAdjustments();return;}if(!images.length)return;const statuses={};document.querySelectorAll('input[data-id]').forEach(x=>{statuses[x.dataset.id]=x.checked?'不合格':'合格';});msg.textContent='提交中...';const r=await fetch('/api/product/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({statuses})});const d=await r.json();images=d.images||[];rawImages=d.raw_images||[];product=d.product||null;rawAdjustments={};msg.textContent=`已更新 ${d.updated||0} 张`;renderMetrics(d.metrics||{});renderLabel();}
async function submitRawAdjustments(){const statuses={...rawAdjustments};if(!Object.keys(statuses).length)return;msg.textContent='提交中...';const r=await fetch('/api/product/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({statuses,outward_code:product&&product.outward_code})});const d=await r.json();images=d.images||images;rawImages=d.raw_images||rawImages;product=d.product||product;rawAdjustments={};msg.textContent=`已更新 ${d.updated||0} 张`;renderMetrics(d.metrics||{});renderLabel();}
document.querySelectorAll('.tab').forEach(x=>x.onclick=()=>{activeView=x.dataset.view;msg.textContent='';renderGrid();});menuLabel.onclick=()=>loadProduct();menuStats.onclick=()=>loadStats(1);menuImages.onclick=()=>loadImages(1);searchStats.onclick=()=>loadStats(1);statsSearch.onkeydown=e=>{if(e.key==='Enter')loadStats(1);};searchImages.onclick=()=>loadImages(1);imageSearch.onkeydown=e=>{if(e.key==='Enter')loadImages(1);};prevPage.onclick=()=>loadStats(statsPage-1);nextPage.onclick=()=>loadStats(statsPage+1);prevImagePage.onclick=()=>loadImages(imagePage-1);nextImagePage.onclick=()=>loadImages(imagePage+1);submit.onclick=submitBatch;reload.onclick=()=>activeMenu==='stats'?loadStats(statsPage):(activeMenu==='images'?loadImages(imagePage):loadProduct(product&&product.outward_code));const initialCode=new URLSearchParams(location.search).get('outward_code');loadProduct(initialCode);
</script></body></html>"""
