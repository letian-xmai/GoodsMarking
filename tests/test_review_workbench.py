import csv
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import image_workflow.review_workbench as review_workbench_module
from image_workflow.review_workbench import ReviewState, ReviewWorkbench, apply_review_statuses, read_review_statuses
from image_workflow.review_xlsx import read_workbook_product_summary
from image_workflow.cli import DEFAULT_REVIEW_HOST, DEFAULT_REVIEW_PORT, DEFAULT_STATE_DB, DEFAULT_STATUS_CSV, build_parser
from image_workflow.review_server import _HTML, _batch_payload, _product_payload, _products_payload


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def row_xml(row_number, values):
    return f'<row r="{row_number}">{"".join(cell(f"{chr(65 + index)}{row_number}", value) for index, value in enumerate(values))}</row>'


def write_status_workbook(path, rows):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("_rels/.rels", "")
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}"/>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            (
                f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>'
                + "".join(row_xml(number, values) for number, values in rows)
                + "</sheetData></worksheet>"
            ),
        )


def write_status_csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["outward_code", "image_url", "人工标注状态"])
        writer.writeheader()
        writer.writerows(rows)


def write_product_result(root, outward_code, images):
    product = root / outward_code
    final = product / "最终结果"
    final.mkdir(parents=True)
    with open(product / "manifest.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["row_number", "url", "source", "status", "filename", "error"])
        writer.writeheader()
        for index, item in enumerate(images, start=1):
            writer.writerow({
                "row_number": str(index + 1),
                "url": item["url"],
                "source": "cutout",
                "status": "downloaded",
                "filename": item["source_name"],
                "error": "",
            })
    with open(product / "model_scores.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_name", "selected_final", "result_filename"])
        writer.writeheader()
        for item in images:
            Image.new("RGB", (48, 48), item.get("color", (120, 120, 120))).save(final / item["result_filename"])
            writer.writerow({"source_name": item["source_name"], "selected_final": "True", "result_filename": item["result_filename"]})


def write_raw_images(root, outward_code, images):
    raw_dir = root / outward_code / "商品原始照片"
    raw_dir.mkdir(parents=True)
    with open(raw_dir / "manifest.csv", "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["row_number", "url", "source", "status", "filename", "error"])
        writer.writeheader()
        for index, item in enumerate(images, start=1):
            filename = item["filename"]
            Image.new("RGB", (48, 48), item.get("color", (120, 120, 120))).save(raw_dir / filename)
            writer.writerow({
                "row_number": str(index + 1),
                "url": item["url"],
                "source": "cutout",
                "status": "downloaded",
                "filename": filename,
                "error": "",
            })


class ReviewWorkbookTests(unittest.TestCase):
    def test_apply_review_statuses_adds_single_manual_status_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "status.xlsx"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是"]),
                    (3, ["CODE1", "http://example.com/b.jpg", "cutout", "已处理", "是"]),
                ],
            )

            apply_review_statuses(workbook, {("CODE1", "http://example.com/a.jpg"): "不合格"})
            statuses = read_review_statuses(workbook, {("CODE1", "http://example.com/a.jpg"), ("CODE1", "http://example.com/b.jpg")})

        self.assertEqual(statuses[("CODE1", "http://example.com/a.jpg")], "不合格")
        self.assertNotIn(("CODE1", "http://example.com/b.jpg"), statuses)

    def test_workbook_product_summary_counts_standard_and_cutout_urls_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "status.xlsx"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source"]),
                    (2, ["CODE1", "http://example.com/a-standard.jpg", "standard"]),
                    (3, ["CODE1", "http://example.com/a-standard.jpg", "standard"]),
                    (4, ["CODE1", "http://example.com/a-cutout.jpg", "cutout"]),
                    (5, ["CODE2", "http://example.com/b-standard.jpg", "standard"]),
                ],
            )

            summary = read_workbook_product_summary(workbook)

        self.assertEqual(summary.standard_counts, {"CODE1": 1, "CODE2": 1})
        self.assertEqual(summary.cutout_counts, {"CODE1": 1, "CODE2": 0})

    def test_workbook_product_summary_uses_source_when_standard_url_marker_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "status.xlsx"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source"]),
                    (2, ["CODE1", "http://example.com/ref-a.jpg", "standard"]),
                    (3, ["CODE1", "http://example.com/ref-b.jpg", "standard"]),
                    (4, ["CODE2", "http://example.com/raw-a.jpg", "cutout"]),
                ],
            )

            summary = read_workbook_product_summary(workbook)

        self.assertEqual(summary.standard_counts["CODE1"], 2)
        self.assertEqual(summary.cutout_counts["CODE1"], 0)
        self.assertIn("CODE1", summary.all_standard_product_codes)
        self.assertNotIn("CODE2", summary.all_standard_product_codes)


class ReviewWorkbenchTests(unittest.TestCase):
    def test_review_workbench_default_debug_address_is_fixed(self):
        args = build_parser().parse_args(["review-workbench"])

        self.assertEqual(DEFAULT_REVIEW_HOST, "127.0.0.1")
        self.assertEqual(DEFAULT_REVIEW_PORT, 8765)
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8765)

    def test_state_snapshot_nonblocking_starts_background_state_build(self):
        class AsyncWorkbench(ReviewWorkbench):
            def __init__(self, root: Path):
                super().__init__(root / "missing-results", root / "missing.xlsx")
                self.started = threading.Event()
                self.release = threading.Event()

            def build_state(self):
                self.started.set()
                self.release.wait(2)
                return ReviewState(
                    [],
                    {"total_products": 0, "completed_products": 0, "invalid_products": 0, "unfinished_products": 0},
                    {},
                    set(),
                    set(),
                    None,
                )

        with tempfile.TemporaryDirectory() as tmp:
            workbench = AsyncWorkbench(Path(tmp))

            self.assertIsNone(workbench.state_snapshot(blocking=False))
            self.assertTrue(workbench.started.wait(1))
            self.assertIsNone(workbench.state_snapshot(blocking=False))
            workbench.release.set()
            deadline = time.time() + 2
            state = None
            while state is None and time.time() < deadline:
                state = workbench.state_snapshot(blocking=False)
                time.sleep(0.01)

        self.assertIsNotNone(state)

    def test_total_products_comes_from_workbook_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", ""]),
                    (3, ["CODE2", "http://example.com/standard-main.jpg", "standard", "", "否", ""]),
                    (4, ["CODE3", "http://example.com/c.jpg", "cutout", "已处理", "是", ""]),
                    (5, ["CODE4", "http://example.com/standard-ref.jpg", "standard", "", "否", ""]),
                    (6, ["CODE4", "http://example.com/d.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                ],
            )
            write_product_result(
                result_root,
                "EXTRA",
                [
                    {"url": "http://example.com/extra.jpg", "source_name": "r000003__cutout__bbbb.jpg", "result_filename": "01_front_label__001__r000003__cutout__bbbb.jpg"},
                ],
            )

            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            state = workbench.build_state()
            batch = workbench.next_batch(state)

        self.assertEqual(state.metrics, {"total_products": 4, "completed_products": 0, "invalid_products": 1, "pending_annotation_products": 1, "unfinished_products": 3})
        self.assertEqual([item.outward_code for item in batch], ["CODE1"])

    def test_review_workbench_reads_and_writes_csv_status_without_xlsx_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_root = root / "商品标注结果"
            status_csv = root / "manual_status.csv"
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                    {"url": "http://example.com/b.jpg", "source_name": "r000003__cutout__bbbb.jpg", "result_filename": "02_side__001__r000003__cutout__bbbb.jpg"},
                ],
            )
            write_status_csv(status_csv, [{"outward_code": "CODE1", "image_url": "http://example.com/a.jpg", "人工标注状态": "合格"}])

            workbench = ReviewWorkbench(result_root, status_file=status_csv, batch_size=20)
            state = workbench.build_state()
            pending = next(item for item in state.products[0].images if item.image_url == "http://example.com/b.jpg")
            result = workbench.submit_product_statuses({pending.review_id: "不合格"})
            reloaded = ReviewWorkbench(result_root, status_file=status_csv, batch_size=20).build_state()

        self.assertEqual(result["updated"], 1)
        self.assertEqual({image.image_url: image.review_status for image in reloaded.products[0].images}, {
            "http://example.com/a.jpg": "合格",
            "http://example.com/b.jpg": "不合格",
        })

    def test_review_workbench_syncs_manual_statuses_to_sqlite_state_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_root = root / "商品标注结果"
            status_csv = root / "manual_status.csv"
            state_db = root / "goods_marking.db"
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                ],
            )

            workbench = ReviewWorkbench(result_root, status_file=status_csv, state_db=state_db, batch_size=20)
            image = workbench.build_state().products[0].images[0]
            workbench.submit_product_statuses({image.review_id: "合格"})
            reloaded = ReviewWorkbench(result_root, status_file=status_csv, state_db=state_db, batch_size=20).build_state()

        self.assertEqual(reloaded.products[0].images[0].review_status, "合格")

    def test_review_workbench_can_use_sqlite_state_db_without_csv_status_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_root = root / "商品标注结果"
            state_db = root / "goods_marking.db"
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                ],
            )

            workbench = ReviewWorkbench(result_root, status_file=None, state_db=state_db, batch_size=20)
            image = workbench.build_state().products[0].images[0]
            workbench.submit_product_statuses({image.review_id: "不合格"})
            reloaded = ReviewWorkbench(result_root, status_file=None, state_db=state_db, batch_size=20).build_state()

        self.assertEqual(reloaded.products[0].images[0].review_status, "不合格")

    def test_all_standard_product_is_invalid_even_when_urls_do_not_contain_standard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片"]),
                    (2, ["CODE1", "http://example.com/ref-a.jpg", "standard", "未处理", "否"]),
                    (3, ["CODE1", "http://example.com/ref-b.jpg", "standard", "未处理", "否"]),
                    (4, ["CODE2", "http://example.com/raw-a.jpg", "cutout", "未处理", "否"]),
                ],
            )
            write_raw_images(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/ref-a.jpg", "filename": "r000001__standard__aaaa.jpg"},
                    {"url": "http://example.com/ref-b.jpg", "filename": "r000002__standard__bbbb.jpg"},
                ],
            )

            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            state = workbench.build_state()
            rows = workbench.product_summaries()

        self.assertEqual(state.metrics, {"total_products": 2, "completed_products": 0, "invalid_products": 1, "pending_annotation_products": 0, "unfinished_products": 1})
        self.assertEqual(rows[0]["outward_code"], "CODE1")
        self.assertEqual(rows[0]["standard_count"], 2)
        self.assertEqual(rows[0]["cutout_count"], 0)
        self.assertEqual(rows[0]["status"], "无效商品")

    def test_metrics_count_pending_annotation_products_with_final_images_and_no_manual_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/pending-a.jpg", "cutout", "已处理", "是", ""]),
                    (3, ["CODE2", "http://example.com/partial-a.jpg", "cutout", "已处理", "是", "合格"]),
                    (4, ["CODE2", "http://example.com/partial-b.jpg", "cutout", "已处理", "是", ""]),
                    (5, ["CODE3", "http://example.com/done-a.jpg", "cutout", "已处理", "是", "合格"]),
                    (6, ["CODE4", "http://example.com/ref-a.jpg", "standard", "未处理", "否", ""]),
                    (7, ["CODE5", "http://example.com/no-final-a.jpg", "cutout", "未处理", "否", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [{"url": "http://example.com/pending-a.jpg", "source_name": "r1.jpg", "result_filename": "01__r1.jpg"}],
            )
            write_product_result(
                result_root,
                "CODE2",
                [
                    {"url": "http://example.com/partial-a.jpg", "source_name": "r2.jpg", "result_filename": "01__r2.jpg"},
                    {"url": "http://example.com/partial-b.jpg", "source_name": "r3.jpg", "result_filename": "02__r3.jpg"},
                ],
            )
            write_product_result(
                result_root,
                "CODE3",
                [{"url": "http://example.com/done-a.jpg", "source_name": "r4.jpg", "result_filename": "01__r4.jpg"}],
            )

            state = ReviewWorkbench(result_root, workbook, batch_size=20).build_state()

        self.assertEqual(state.metrics["pending_annotation_products"], 1)
        self.assertEqual(state.metrics["completed_products"], 1)
        self.assertEqual(state.metrics["invalid_products"], 1)
        self.assertEqual(state.metrics["unfinished_products"], 3)

    def test_next_batch_returns_whole_unfinished_product_for_status_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", "合格"]),
                    (3, ["CODE1", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                    (4, ["CODE2", "http://example.com/c.jpg", "cutout", "已处理", "是", "不合格"]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                    {"url": "http://example.com/b.jpg", "source_name": "r000003__cutout__bbbb.jpg", "result_filename": "02_back_barcode__002__r000003__cutout__bbbb.jpg"},
                ],
            )
            write_product_result(
                result_root,
                "CODE2",
                [
                    {"url": "http://example.com/c.jpg", "source_name": "r000004__cutout__cccc.jpg", "result_filename": "01_front_label__001__r000004__cutout__cccc.jpg"},
                ],
            )
            (result_root / "CODE3").mkdir(parents=True)

            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            state = workbench.build_state()
            batch = workbench.next_batch(state)

        self.assertEqual(state.metrics, {"total_products": 2, "completed_products": 0, "invalid_products": 1, "pending_annotation_products": 0, "unfinished_products": 1})
        self.assertEqual([item.image_url for item in batch], ["http://example.com/a.jpg", "http://example.com/b.jpg"])

    def test_submit_marks_checked_invalid_and_unchecked_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是"]),
                    (3, ["CODE1", "http://example.com/b.jpg", "cutout", "已处理", "是"]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                    {"url": "http://example.com/b.jpg", "source_name": "r000003__cutout__bbbb.jpg", "result_filename": "02_back_barcode__002__r000003__cutout__bbbb.jpg"},
                ],
            )
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            state = workbench.build_state()
            batch = workbench.next_batch(state)

            summary = workbench.submit_batch([item.review_id for item in batch], {batch[1].review_id})
            statuses = read_review_statuses(workbook, {("CODE1", "http://example.com/a.jpg"), ("CODE1", "http://example.com/b.jpg")})

        self.assertEqual(summary["updated"], 2)
        self.assertEqual(statuses[("CODE1", "http://example.com/a.jpg")], "合格")
        self.assertEqual(statuses[("CODE1", "http://example.com/b.jpg")], "不合格")

    def test_product_summaries_include_counts_status_and_action_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/standard-1.jpg", "standard", "", "否", ""]),
                    (3, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", "合格"]),
                    (4, ["CODE1", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                    (5, ["CODE2", "http://example.com/standard-2.jpg", "standard", "", "否", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                    {"url": "http://example.com/b.jpg", "source_name": "r000003__cutout__bbbb.jpg", "result_filename": "02_back_barcode__002__r000003__cutout__bbbb.jpg"},
                ],
            )

            rows = ReviewWorkbench(result_root, workbook, batch_size=20).product_summaries()

        self.assertEqual(
            rows,
            [
                {
                    "outward_code": "CODE1",
                    "standard_count": 1,
                    "cutout_count": 2,
                    "final_count": 2,
                    "manual_count": 1,
                    "status": "标注中",
                    "action": "去标注",
                },
                {
                    "outward_code": "CODE2",
                    "standard_count": 1,
                    "cutout_count": 0,
                    "final_count": 0,
                    "manual_count": 0,
                    "status": "无效商品",
                    "action": "去标注",
                },
            ],
        )

    def test_product_payload_defaults_to_first_unfinished_product_and_can_select_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", "合格"]),
                    (3, ["CODE2", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                    (4, ["CODE3", "http://example.com/c.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(result_root, "CODE1", [{"url": "http://example.com/a.jpg", "source_name": "r1.jpg", "result_filename": "01__r1.jpg"}])
            write_product_result(result_root, "CODE2", [{"url": "http://example.com/b.jpg", "source_name": "r2.jpg", "result_filename": "01__r2.jpg"}])
            write_product_result(result_root, "CODE3", [{"url": "http://example.com/c.jpg", "source_name": "r3.jpg", "result_filename": "01__r3.jpg"}])
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)

            default_payload = _product_payload(workbench)
            selected_payload = _product_payload(workbench, "CODE3")

        self.assertEqual(default_payload["product"]["outward_code"], "CODE2")
        self.assertEqual(default_payload["product"]["status"], "待标注")
        self.assertEqual([item["review_status"] for item in default_payload["images"]], [""])
        self.assertEqual(selected_payload["product"]["outward_code"], "CODE3")

    def test_submit_product_statuses_can_change_existing_status_and_auto_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", "合格"]),
                    (3, ["CODE2", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(result_root, "CODE1", [{"url": "http://example.com/a.jpg", "source_name": "r1.jpg", "result_filename": "01__r1.jpg"}])
            write_product_result(result_root, "CODE2", [{"url": "http://example.com/b.jpg", "source_name": "r2.jpg", "result_filename": "01__r2.jpg"}])
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            code1_image = _product_payload(workbench, "CODE1")["images"][0]

            result = workbench.submit_product_statuses({code1_image["review_id"]: "不合格"})
            statuses = read_review_statuses(workbook, {("CODE1", "http://example.com/a.jpg"), ("CODE2", "http://example.com/b.jpg")})
            next_payload = _product_payload(workbench)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(statuses[("CODE1", "http://example.com/a.jpg")], "不合格")
        self.assertEqual(next_payload["product"]["outward_code"], "CODE2")

    def test_submit_product_statuses_does_not_rewrite_workbook_synchronously(self):
        original_apply = review_workbench_module.apply_review_statuses

        def fail_sync_rewrite(*_args, **_kwargs):
            raise AssertionError("submit should not rewrite the xlsx during the request")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", ""]),
                    (3, ["CODE2", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(result_root, "CODE1", [{"url": "http://example.com/a.jpg", "source_name": "r1.jpg", "result_filename": "01__r1.jpg"}])
            write_product_result(result_root, "CODE2", [{"url": "http://example.com/b.jpg", "source_name": "r2.jpg", "result_filename": "01__r2.jpg"}])
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            image = _product_payload(workbench, "CODE1")["images"][0]

            try:
                review_workbench_module.apply_review_statuses = fail_sync_rewrite
                result = workbench.submit_product_statuses({image["review_id"]: "合格"})
            finally:
                review_workbench_module.apply_review_statuses = original_apply

            reloaded = _product_payload(ReviewWorkbench(result_root, workbook, batch_size=20), "CODE1")

        self.assertEqual(result["updated"], 1)
        self.assertEqual(reloaded["images"][0]["review_status"], "合格")

    def test_submit_product_statuses_marks_all_images_reviewed_and_product_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", ""]),
                    (3, ["CODE1", "http://example.com/b.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r1.jpg", "result_filename": "01__r1.jpg"},
                    {"url": "http://example.com/b.jpg", "source_name": "r2.jpg", "result_filename": "02__r2.jpg"},
                ],
            )
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            images = _product_payload(workbench, "CODE1")["images"]

            result = workbench.submit_product_statuses({item["review_id"]: "合格" for item in images})
            statuses = read_review_statuses(workbook, {("CODE1", "http://example.com/a.jpg"), ("CODE1", "http://example.com/b.jpg")})
            payload = _product_payload(workbench, "CODE1")

        self.assertEqual(result["updated"], 2)
        self.assertEqual(set(statuses.values()), {"合格"})
        self.assertEqual(payload["product"]["status"], "已完成")

    def test_product_payload_includes_original_image_review_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/raw-a.jpg", "cutout", "已处理", "否", "不合格"]),
                    (3, ["CODE1", "http://example.com/raw-b.jpg", "cutout", "已处理", "否", ""]),
                ],
            )
            write_raw_images(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/raw-a.jpg", "filename": "r000001__cutout__aaaa.jpg"},
                    {"url": "http://example.com/raw-b.jpg", "filename": "r000002__cutout__bbbb.jpg"},
                ],
            )

            payload = _product_payload(ReviewWorkbench(result_root, workbook, batch_size=20), "CODE1")

        self.assertEqual([item["review_status"] for item in payload["raw_images"]], ["不合格", ""])

    def test_product_payload_marks_original_images_present_in_final_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/raw-a.jpg", "cutout", "已处理", "是", ""]),
                    (3, ["CODE1", "http://example.com/raw-b.jpg", "cutout", "已处理", "否", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/raw-a.jpg", "source_name": "r000001__cutout__aaaa.jpg", "result_filename": "01__r000001__cutout__aaaa.jpg"},
                ],
            )
            write_raw_images(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/raw-a.jpg", "filename": "r000001__cutout__aaaa.jpg"},
                    {"url": "http://example.com/raw-b.jpg", "filename": "r000002__cutout__bbbb.jpg"},
                ],
            )

            payload = _product_payload(ReviewWorkbench(result_root, workbook, batch_size=20), "CODE1")

        self.assertEqual([item["review_status"] for item in payload["raw_images"]], ["", ""])
        self.assertEqual([item["in_final_result"] for item in payload["raw_images"]], [True, False])

    def test_final_result_named_from_url_basename_uses_manifest_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            product = result_root / "CODE1"
            final = product / "最终结果"
            final.mkdir(parents=True)
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/assets/frame_001.jpg", "cutout", "已处理", "是", "合格"]),
                ],
            )
            with open(product / "manifest.csv", "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["row_number", "url", "source", "status", "filename", "error"])
                writer.writeheader()
                writer.writerow({
                    "row_number": "2",
                    "url": "http://example.com/assets/frame_001.jpg",
                    "source": "cutout",
                    "status": "downloaded",
                    "filename": "r000001__cutout__aaaa.jpg",
                    "error": "",
                })
            Image.new("RGB", (48, 48), (120, 120, 120)).save(final / "01_manual__001__frame_001.jpg")

            payload = _product_payload(ReviewWorkbench(result_root, workbook, batch_size=20), "CODE1")

        self.assertEqual(payload["images"][0]["image_url"], "http://example.com/assets/frame_001.jpg")
        self.assertEqual(payload["images"][0]["review_status"], "合格")

    def test_submit_product_statuses_updates_original_image_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/raw-a.jpg", "cutout", "已处理", "否", ""]),
                    (3, ["CODE1", "http://example.com/raw-b.jpg", "cutout", "已处理", "否", "不合格"]),
                ],
            )
            write_raw_images(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/raw-a.jpg", "filename": "r000001__cutout__aaaa.jpg"},
                    {"url": "http://example.com/raw-b.jpg", "filename": "r000002__cutout__bbbb.jpg"},
                ],
            )
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            raw_images = _product_payload(workbench, "CODE1")["raw_images"]

            result = workbench.submit_product_statuses({
                raw_images[0]["review_id"]: "合格",
                raw_images[1]["review_id"]: "合格",
            })
            statuses = read_review_statuses(workbook, {
                ("CODE1", "http://example.com/raw-a.jpg"),
                ("CODE1", "http://example.com/raw-b.jpg"),
            })

        self.assertEqual(result["updated"], 2)
        self.assertEqual(statuses, {
            ("CODE1", "http://example.com/raw-a.jpg"): "合格",
            ("CODE1", "http://example.com/raw-b.jpg"): "合格",
        })

    def test_valid_original_image_is_promoted_to_final_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/raw-a.jpg", "cutout", "已处理", "否", ""]),
                ],
            )
            write_raw_images(
                result_root,
                "CODE1",
                [{"url": "http://example.com/raw-a.jpg", "filename": "r000001__cutout__aaaa.jpg"}],
            )
            workbench = ReviewWorkbench(result_root, workbook, batch_size=20)
            raw_image = _product_payload(workbench, "CODE1")["raw_images"][0]

            result = workbench.submit_product_statuses({raw_image["review_id"]: "合格"})
            reloaded = ReviewWorkbench(result_root, workbook, batch_size=20)
            payload = _product_payload(reloaded, "CODE1")
            summary = reloaded.product_summaries()[0]

        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(payload["images"]), 1)
        self.assertTrue(payload["images"][0]["result_filename"].endswith("r000001__cutout__aaaa.jpg"))
        self.assertEqual(payload["images"][0]["review_status"], "合格")
        self.assertEqual(summary["final_count"], 1)
        self.assertEqual(summary["manual_count"], 1)
        self.assertEqual(summary["status"], "已完成")

    def test_products_payload_returns_statistics_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(workbook, [(1, ["outward_code", "image_url"]), (2, ["CODE1", "http://example.com/a.jpg"])])

            payload = _products_payload(ReviewWorkbench(result_root, workbook, batch_size=20))

        self.assertEqual(payload["products"][0]["outward_code"], "CODE1")
        self.assertEqual(payload["products"][0]["status"], "无最终结果")

    def test_products_payload_can_return_loading_without_blocking_on_state_build(self):
        class LoadingWorkbench:
            def __init__(self):
                self.blocking_arg = None

            def state_snapshot(self, blocking=True):
                self.blocking_arg = blocking
                return None

        workbench = LoadingWorkbench()

        payload = _products_payload(workbench, blocking=False)

        self.assertFalse(workbench.blocking_arg)
        self.assertTrue(payload["loading"])
        self.assertEqual(payload["products"], [])
        self.assertEqual(payload["pagination"]["page_size"], 50)

    def test_products_payload_paginates_statistics_rows_by_50(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            rows = [(1, ["outward_code", "image_url"])]
            rows.extend((index + 1, [f"CODE{index:03d}", f"http://example.com/{index}.jpg"]) for index in range(1, 56))
            write_status_workbook(workbook, rows)

            first_page = _products_payload(ReviewWorkbench(result_root, workbook, batch_size=20))
            second_page = _products_payload(ReviewWorkbench(result_root, workbook, batch_size=20), page=2)

        self.assertEqual(first_page["pagination"], {"page": 1, "page_size": 50, "total": 55, "total_pages": 2, "query": ""})
        self.assertEqual(len(first_page["products"]), 50)
        self.assertEqual(first_page["products"][0]["outward_code"], "CODE001")
        self.assertEqual(len(second_page["products"]), 5)
        self.assertEqual(second_page["products"][0]["outward_code"], "CODE051")

    def test_products_payload_filters_by_product_code_query_before_paging(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url"]),
                    (2, ["ABC001", "http://example.com/a.jpg"]),
                    (3, ["ABC002", "http://example.com/b.jpg"]),
                    (4, ["XYZ001", "http://example.com/c.jpg"]),
                ],
            )

            payload = _products_payload(ReviewWorkbench(result_root, workbook, batch_size=20), query="abc")

        self.assertEqual([row["outward_code"] for row in payload["products"]], ["ABC001", "ABC002"])
        self.assertEqual(payload["pagination"], {"page": 1, "page_size": 50, "total": 2, "total_pages": 1, "query": "abc"})

    def test_batch_payload_includes_current_product_status_and_original_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "status.xlsx"
            result_root = root / "商品标注结果"
            write_status_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source", "图片处理进度", "最终结果是否包含该图片", "人工标注状态"]),
                    (2, ["CODE1", "http://example.com/a.jpg", "cutout", "已处理", "是", ""]),
                ],
            )
            write_product_result(
                result_root,
                "CODE1",
                [
                    {"url": "http://example.com/a.jpg", "source_name": "r000002__cutout__aaaa.jpg", "result_filename": "01_front_label__001__r000002__cutout__aaaa.jpg"},
                ],
            )
            raw_dir = result_root / "CODE1" / "商品原始照片"
            raw_dir.mkdir()
            Image.new("RGB", (48, 48), (40, 80, 120)).save(raw_dir / "r000002__cutout__aaaa.jpg")
            Image.new("RGB", (48, 48), (80, 120, 40)).save(raw_dir / "r000003__cutout__bbbb.jpg")

            payload = _batch_payload(ReviewWorkbench(result_root, workbook, batch_size=20))

        self.assertEqual(payload["product"], {"outward_code": "CODE1", "status": "待标注"})
        self.assertEqual([item["result_filename"] for item in payload["raw_images"]], ["r000002__cutout__aaaa.jpg", "r000003__cutout__bbbb.jpg"])
        self.assertTrue(all(item["image_src"].startswith("/image/") for item in payload["raw_images"]))

    def test_cli_parses_review_workbench_command(self):
        args = build_parser().parse_args([
            "review-workbench",
            "--source-workbook",
            "source.xlsx",
            "--status-csv",
            "status.csv",
            "--state-db",
            "state.db",
            "--result-dir",
            "商品标注结果",
            "--port",
            "8999",
            "--batch-size",
            "12",
        ])

        self.assertEqual(args.command, "review-workbench")
        self.assertEqual(args.source_workbook, "source.xlsx")
        self.assertEqual(args.status_csv, "status.csv")
        self.assertEqual(args.state_db, "state.db")
        self.assertEqual(args.result_dir, "商品标注结果")
        self.assertEqual(args.port, 8999)
        self.assertEqual(args.batch_size, 12)

    def test_cli_review_workbench_defaults_to_csv_status_without_source_workbook(self):
        args = build_parser().parse_args(["review-workbench"])

        self.assertEqual(args.source_workbook, "")
        self.assertEqual(args.status_csv, DEFAULT_STATUS_CSV)
        self.assertEqual(args.state_db, DEFAULT_STATE_DB)

    def test_workbench_html_toggles_invalid_checkbox_when_image_clicked(self):
        self.assertIn("review-image", _HTML)
        self.assertIn("toggleInvalid", _HTML)
        self.assertIn("card.classList.toggle('selected'", _HTML)

    def test_workbench_html_defaults_final_images_to_valid_checkbox_invalid(self):
        self.assertIn('type="checkbox" data-id="${it.review_id}"', _HTML)
        self.assertIn('class="statusText"', _HTML)
        self.assertIn("querySelector('.statusText').textContent=checkbox.checked?'不合格':'合格'", _HTML)
        self.assertIn("statuses[x.dataset.id]=x.checked?'不合格':'合格'", _HTML)
        self.assertNotIn("<select data-id=", _HTML)

    def test_workbench_html_has_product_annotation_and_stats_menus(self):
        self.assertIn("商品标注", _HTML)
        self.assertIn("商品统计", _HTML)
        self.assertIn("去标注", _HTML)
        self.assertIn("/api/products", _HTML)
        self.assertIn("/api/product/submit", _HTML)

    def test_workbench_html_displays_pending_annotation_metric(self):
        self.assertIn("待标注商品数", _HTML)
        self.assertIn('id="pendingAnnotation"', _HTML)
        self.assertIn("pendingAnnotation.textContent=m.pending_annotation_products||0", _HTML)

    def test_workbench_html_has_statistics_search_and_pagination_controls(self):
        self.assertIn("每页50个商品", _HTML)
        self.assertIn('id="statsSearch"', _HTML)
        self.assertIn('id="prevPage"', _HTML)
        self.assertIn('id="nextPage"', _HTML)
        self.assertIn("page_size=50", _HTML)

    def test_workbench_html_retries_while_state_is_loading(self):
        self.assertIn("数据加载中", _HTML)
        self.assertIn("setTimeout(()=>loadProduct(code),2000)", _HTML)
        self.assertIn("setTimeout(()=>loadStats(page),2000)", _HTML)

    def test_workbench_html_loads_product_code_from_url_query(self):
        self.assertIn("new URLSearchParams(location.search).get('outward_code')", _HTML)
        self.assertIn("loadProduct(initialCode)", _HTML)

    def test_workbench_html_has_final_and_original_tabs(self):
        self.assertIn("模型最终结果", _HTML)
        self.assertIn("商品原始照片", _HTML)
        self.assertIn("renderOriginalImages", _HTML)

    def test_workbench_html_supports_original_image_status_adjustments(self):
        self.assertIn("rawAdjustments", _HTML)
        self.assertIn("toggleRawStatus", _HTML)
        self.assertIn("未标注", _HTML)
        self.assertIn("合格待确认", _HTML)
        self.assertIn("pending-valid", _HTML)
        self.assertIn("提交调整", _HTML)
        self.assertIn("item.in_final_result?'合格待确认':'未标注'", _HTML)
        self.assertIn("status==='未标注'||status==='不合格'||status==='合格待确认'?'合格':'不合格'", _HTML)

    def test_workbench_html_places_tabs_below_product_status(self):
        self.assertIn('class="productbar"', _HTML)
        self.assertIn('class="tabsbar"', _HTML)
        self.assertIn('role="tablist"', _HTML)
        self.assertLess(_HTML.index('class="productbar"'), _HTML.index('class="tabsbar"'))

    def test_workbench_actions_float_at_bottom_center(self):
        self.assertIn('class="actions"', _HTML)
        self.assertIn('.actions{position:fixed', _HTML)
        self.assertIn('bottom:18px', _HTML)
        self.assertIn('left:50%', _HTML)
        self.assertIn('transform:translateX(-50%)', _HTML)
        self.assertIn('padding-bottom:92px', _HTML)


if __name__ == "__main__":
    unittest.main()
