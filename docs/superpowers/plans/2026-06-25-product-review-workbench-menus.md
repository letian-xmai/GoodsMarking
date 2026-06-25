# Product Review Workbench Menus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two workbench menus, `商品标注` and `商品统计`, with product-level annotation flow and per-product statistics.

**Architecture:** Keep the existing standard-library HTTP workbench and extend `ReviewWorkbench` with product summary and product-specific batch operations. Reuse the status workbook read/write path so `人工标注状态` remains the single persisted manual review field.

**Tech Stack:** Python standard library, existing XLSX ZIP/XML helpers, existing `商品标注结果/{outward_code}/最终结果` artifacts.

---

### Task 1: Product Summary State

**Files:**
- Modify: `tests/test_review_workbench.py`
- Modify: `image_workflow/review_workbench.py`

- [ ] Add tests for per-product summary rows, including `standard` vs non-`standard` URL counts from the status workbook.
- [ ] Add summary dataclass and workbook URL classification logic.
- [ ] Run `python3 -m unittest tests.test_review_workbench`.

### Task 2: Product-Level Annotation Flow

**Files:**
- Modify: `tests/test_review_workbench.py`
- Modify: `image_workflow/review_workbench.py`

- [ ] Add tests for default next unfinished product, selecting a product by code, and submitting status changes for already-marked images.
- [ ] Add product-specific image lookup and submission methods.
- [ ] Run `python3 -m unittest tests.test_review_workbench`.

### Task 3: HTTP API And Two-Menu UI

**Files:**
- Modify: `tests/test_review_workbench.py`
- Modify: `image_workflow/review_server.py`

- [ ] Add tests that the HTML exposes `商品标注`, `商品统计`, `去标注`, and product-specific API calls.
- [ ] Add `GET /api/products`, `GET /api/product?outward_code=...`, and `POST /api/product/submit`.
- [ ] Replace the single batch UI with two menu views.
- [ ] Run `python3 -m unittest tests.test_review_workbench`.

### Task 4: Verification

**Files:**
- No new files.

- [ ] Run `python3 -m unittest tests.test_review_workbench`.
- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `python3 -m compileall image_workflow tests`.
