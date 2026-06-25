from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
import argparse
import csv
import sys

from .downloader import download_group
from .excel_reader import inspect_workbook, iter_excel_records, records_for_group
from .full_evaluation import evaluate_full_testset
from .group_index import build_group_index, iter_group_files, read_group_records
from .progress import ProgressTable
from .review_server import run_review_workbench
from .review_workbench import STATUS_HEADER
from .selection import select_downloaded_group
from .state_db import StateDb
from .training_set import build_training_dataset
from .verification import verify_group


DEFAULT_XLSX = "2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0.xlsx"
DEFAULT_STATUS_CSV = "2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度_人工标注状态.csv"
DEFAULT_STATE_DB = "goods_marking.db"
DEFAULT_REVIEW_HOST = "127.0.0.1"
DEFAULT_REVIEW_PORT = 8765


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {
        "inspect": command_inspect,
        "run-one": command_run_one,
        "run-full": command_run_full,
        "verify": command_verify,
        "build-training-set": command_build_training_set,
        "evaluate-full-testset": command_evaluate_full_testset,
        "review-workbench": command_review_workbench,
        "migrate-state": command_migrate_state,
    }.get(args.command)
    if handler is not None:
        return handler(args)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="商品图片分组下载与筛选工作流")
    parser.add_argument("--workbook", default=DEFAULT_XLSX)
    parser.add_argument("--original-dir", default="商品原始照片")
    parser.add_argument("--result-dir", default="商品标注结果")
    parser.add_argument("--state-db", default=DEFAULT_STATE_DB)
    parser.add_argument("--target-count", type=int, default=40)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("inspect")
    one = sub.add_parser("run-one")
    one.add_argument("--outward-code", required=True)
    one.add_argument("--download-workers", type=int, default=4)
    full = sub.add_parser("run-full")
    full.add_argument("--confirmed", action="store_true")
    full.add_argument("--group-workers", type=int, default=3)
    full.add_argument("--download-workers", type=int, default=4)
    full.add_argument("--index-dir", default=".image_workflow_cache")
    full.add_argument("--rebuild-index", action="store_true")
    full.add_argument("--limit", type=int)
    verify = sub.add_parser("verify")
    verify.add_argument("--outward-code", required=True)
    training = sub.add_parser("build-training-set")
    training.add_argument("--label-workbook", required=True)
    training.add_argument("--output-dir", default="模型训练数据")
    training.add_argument("--download-workers", type=int, default=4)
    evaluate = sub.add_parser("evaluate-full-testset")
    evaluate.add_argument("--dataset-dir", default="模型训练数据")
    evaluate.add_argument("--model-dir", default="模型训练数据/model")
    evaluate.add_argument("--run-id")
    evaluate.add_argument("--no-preview", dest="preview", action="store_false")
    evaluate.set_defaults(preview=True)
    review = sub.add_parser("review-workbench")
    review.add_argument("--source-workbook", default="")
    review.add_argument("--state-db", default=DEFAULT_STATE_DB)
    review.add_argument("--result-dir", default="商品标注结果")
    review.add_argument("--host", default=DEFAULT_REVIEW_HOST)
    review.add_argument("--port", type=int, default=DEFAULT_REVIEW_PORT)
    review.add_argument("--batch-size", type=int, default=40)
    migrate = sub.add_parser("migrate-state")
    migrate.add_argument("--progress", default="workflow_progress.csv")
    migrate.add_argument("--status-csv", default=DEFAULT_STATUS_CSV)
    migrate.add_argument("--source-workbook", default="")
    return parser


def command_inspect(args) -> int:
    summary = inspect_workbook(args.workbook)
    ProgressTable(args.state_db).initialize_pending(dict(summary.group_counts))
    print(f"total_urls={summary.total_urls}")
    print(f"group_count={len(summary.group_counts)}")
    print(f"state_db={Path(args.state_db).resolve()}")
    return 0


def command_run_one(args) -> int:
    records = records_for_group(args.workbook, args.outward_code)
    if not records:
        print(f"未找到 outward_code: {args.outward_code}", file=sys.stderr)
        return 1
    report = process_group(records, Path(args.original_dir), Path(args.result_dir), ProgressTable(args.state_db), args.target_count, args.download_workers)
    print_group_report(report)
    return 0 if report["download_complete"] else 1


def command_run_full(args) -> int:
    if not args.confirmed:
        print("run-full 需要显式添加 --confirmed；请先用 run-one 确认试跑结果。", file=sys.stderr)
        return 2
    progress = ProgressTable(args.state_db)
    index_dir = Path(args.index_dir)
    if args.rebuild_index or not (index_dir / "group_index.csv").exists():
        summary = build_group_index(args.workbook, index_dir, args.state_db, overwrite=args.rebuild_index)
        print(f"index_built total_records={summary.total_records} group_count={summary.group_count}")
    files = iter_group_files(index_dir)
    if args.limit:
        files = files[:args.limit]
    lock = Lock()
    with ThreadPoolExecutor(max_workers=max(1, args.group_workers)) as executor:
        futures = [executor.submit(_process_group_file, path, args, progress, lock) for path in files]
        for future in as_completed(futures):
            print_group_report(future.result())
    return 0


def command_verify(args) -> int:
    original = Path(args.original_dir) / args.outward_code
    result = Path(args.result_dir) / args.outward_code
    report = verify_group(args.outward_code, original, result, target_count=args.target_count)
    print(f"ok={report['ok']} issues={';'.join(report['issues'])}")
    return 0 if report["ok"] else 1


def command_build_training_set(args) -> int:
    summary = build_training_dataset(args.label_workbook, args.output_dir, download_workers=args.download_workers)
    print(f"unique_items={summary['unique_items']} group_count={summary['group_count']} output={Path(args.output_dir).resolve()}")
    return 0


def command_evaluate_full_testset(args) -> int:
    summary = evaluate_full_testset(args.dataset_dir, args.model_dir, write_preview=args.preview, run_id=args.run_id)
    print(f"samples={summary['samples']} products={summary['products']} mismatches={summary['mismatches']} accuracy={summary['accuracy']:.6f} run_id={summary['model_run']['run_id']} run_root={summary['model_run']['run_root']} report={summary['paths']['report']}")
    return 0


def command_review_workbench(args) -> int:
    run_review_workbench(args.result_dir, None, state_db=args.state_db, host=args.host, port=args.port, batch_size=args.batch_size)
    return 0


def command_migrate_state(args) -> int:
    db = StateDb(args.state_db)
    source_count = 0
    if args.source_workbook:
        def source_rows():
            nonlocal source_count
            for record in iter_excel_records(args.source_workbook):
                source_count += 1
                yield {
                    "outward_code": record.outward_code,
                    "image_url": record.image_url,
                    "source": record.source,
                    "row_number": str(record.row_number),
                }

        db.upsert_product_images(source_rows())
    progress_rows = _read_csv_rows(Path(args.progress), encoding="utf-8")
    if progress_rows:
        db.replace_progress(progress_rows)
    status_updates = {}
    for row in _read_csv_rows(Path(args.status_csv), encoding="utf-8-sig"):
        outward_code = str(row.get("outward_code", "")).strip()
        image_url = str(row.get("image_url", "")).strip()
        status = str(row.get(STATUS_HEADER, "")).strip()
        if outward_code and image_url:
            status_updates[(outward_code, image_url)] = status
    db.upsert_review_statuses(status_updates)
    print(f"migrated_source_images={source_count} migrated_progress={len(progress_rows)} migrated_review_statuses={len(status_updates)} state_db={Path(args.state_db).resolve()}")
    return 0


def _read_csv_rows(path: Path, encoding: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, newline="", encoding=encoding) as handle:
        return list(csv.DictReader(handle))


def process_group(records, original_root: Path, result_root: Path, progress: ProgressTable, target_count: int, download_workers: int, progress_lock: Lock | None = None) -> dict:
    outward_code = records[0].outward_code
    original = original_root / outward_code
    result = result_root / outward_code
    _progress_upsert(progress, progress_lock, outward_code=outward_code, status="downloading", total_urls=len(records))
    downloaded = download_group(records, original, workers=download_workers)
    if not downloaded["complete"]:
        _progress_upsert(
            progress,
            progress_lock,
            outward_code=outward_code,
            status="download_incomplete",
            total_urls=len(records),
            downloaded_count=downloaded["downloaded_count"],
            failed_count=downloaded["failed_count"],
            notes="skip selection until downloads are complete",
        )
        return _group_report(outward_code, downloaded, None, None)
    selected = select_downloaded_group(outward_code, original, result, target_count)
    verified = verify_group(outward_code, original, result, target_count=target_count)
    _progress_upsert(
        progress,
        progress_lock,
        outward_code=outward_code,
        status=selected["status"],
        total_urls=len(records),
        downloaded_count=downloaded["downloaded_count"],
        selected_count=selected["selected_count"],
        failed_count=downloaded["failed_count"],
        needs_review=selected["angle_review_needed"],
        notes="angle weak labels; sample review required",
    )
    return _group_report(outward_code, downloaded, selected, verified)


def _process_group_file(path: Path, args, progress: ProgressTable, lock: Lock) -> dict:
    records = read_group_records(path)
    _progress_upsert(progress, lock, outward_code=records[0].outward_code, status="queued", total_urls=len(records))
    return process_group(records, Path(args.original_dir), Path(args.result_dir), progress, args.target_count, args.download_workers, lock)


def _group_report(outward_code: str, downloaded: dict, selected: dict | None, verified: dict | None) -> dict:
    return {
        "outward_code": outward_code,
        "download_complete": downloaded["complete"],
        "downloaded_count": downloaded["downloaded_count"],
        "failed_count": downloaded["failed_count"],
        "selected_count": 0 if selected is None else selected["selected_count"],
        "selection_status": "skipped" if selected is None else selected["status"],
        "verified": False if verified is None else verified["ok"],
    }


def print_group_report(report: dict) -> None:
    print(" ".join(f"{key}={value}" for key, value in report.items()))


def _progress_upsert(progress: ProgressTable, lock: Lock | None, **kwargs) -> None:
    if lock is None:
        progress.upsert(**kwargs)
        return
    with lock:
        progress.upsert(**kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
