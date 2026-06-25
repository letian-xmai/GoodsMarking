# Review Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local review workbench that shows model final images, lets a human mark invalid images in batches, and stores `人工标注状态` in the local SQLite state database with CSV export compatibility.

**Architecture:** Add a focused `image_workflow.review_workbench` module with result scanning, status read/write helpers, batch selection, and a standard-library HTTP server. Wire it into `image_workflow.cli` as `review-workbench`.

**Tech Stack:** Python standard library, SQLite, existing CSV/XLSX compatibility helpers, existing `商品标注结果/{outward_code}/最终结果` artifacts.

---

### Task 1: Review State And Batch Logic

**Files:**
- Create: `image_workflow/review_workbench.py`
- Test: `tests/test_review_workbench.py`

- [ ] Write failing tests for metrics, next-batch selection, and manual status updates.
- [ ] Implement result scanning from `商品标注结果/{outward_code}/最终结果`.
- [ ] Implement status reader/writer for the single `人工标注状态` field.
- [ ] Implement batch submission that marks checked images `不合格` and unchecked images `合格`.

### Task 2: Local HTTP Workbench

**Files:**
- Modify: `image_workflow/review_workbench.py`
- Modify: `image_workflow/cli.py`

- [ ] Add `GET /`, `GET /api/batch`, `POST /api/submit`, and `GET /image/{id}` handlers.
- [ ] Add CLI command `review-workbench` with source-workbook/status-csv/state-db/result-dir/host/port/batch-size options.
- [ ] Verify the command parser exposes the new command.

### Task 3: Verification

**Files:**
- Test: `tests/test_review_workbench.py`

- [ ] Run `python3 -m unittest tests.test_review_workbench`.
- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `python3 -m compileall image_workflow tests`.
