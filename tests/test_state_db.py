import sys
import tempfile
import unittest
from pathlib import Path
import csv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.cli import build_parser, command_migrate_state
from image_workflow.state_db import StateDb


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def row_xml(row_number, values):
    return f'<row r="{row_number}">{"".join(cell(f"{chr(65 + index)}{row_number}", value) for index, value in enumerate(values))}</row>'


def write_source_workbook(path, rows):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("_rels/.rels", "")
        zf.writestr("xl/workbook.xml", f'<workbook xmlns="{NS}"/>')
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>'
            + "".join(row_xml(number, values) for number, values in rows)
            + "</sheetData></worksheet>",
        )


class StateDbTests(unittest.TestCase):
    def test_progress_rows_are_upserted_and_read_in_code_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDb(Path(tmp) / "goods_marking.db")
            db.upsert_progress({
                "outward_code": "CODE2",
                "assignee": "codex",
                "status": "pending",
                "total_urls": "5",
                "downloaded_count": "0",
                "selected_count": "0",
                "failed_count": "0",
                "needs_review": "no",
                "updated_at": "2026-06-25T00:00:00+00:00",
                "notes": "",
            })
            db.upsert_progress({
                "outward_code": "CODE1",
                "assignee": "codex",
                "status": "complete",
                "total_urls": "3",
                "downloaded_count": "3",
                "selected_count": "2",
                "failed_count": "0",
                "needs_review": "yes",
                "updated_at": "2026-06-25T00:00:01+00:00",
                "notes": "checked",
            })
            db.upsert_progress({
                "outward_code": "CODE2",
                "assignee": "codex",
                "status": "shortfall",
                "total_urls": "5",
                "downloaded_count": "5",
                "selected_count": "4",
                "failed_count": "0",
                "needs_review": "no",
                "updated_at": "2026-06-25T00:00:02+00:00",
                "notes": "not enough",
            })

            rows = db.read_progress()

        self.assertEqual([row["outward_code"] for row in rows], ["CODE1", "CODE2"])
        self.assertEqual(rows[1]["status"], "shortfall")
        self.assertEqual(rows[1]["selected_count"], "4")

    def test_review_statuses_are_upserted_and_empty_status_deletes_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDb(Path(tmp) / "goods_marking.db")
            db.upsert_review_statuses({
                ("CODE1", "http://example.com/a.jpg"): "合格",
                ("CODE1", "http://example.com/b.jpg"): "不合格",
                ("CODE2", "http://example.com/c.jpg"): "合格",
            })
            db.upsert_review_statuses({("CODE1", "http://example.com/b.jpg"): ""})

            statuses = db.read_review_statuses({
                ("CODE1", "http://example.com/a.jpg"),
                ("CODE1", "http://example.com/b.jpg"),
            })

        self.assertEqual(statuses, {("CODE1", "http://example.com/a.jpg"): "合格"})

    def test_product_images_keep_first_standard_url_by_row_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDb(Path(tmp) / "goods_marking.db")
            db.upsert_product_images([
                {"outward_code": "CODE1", "image_url": "http://example.com/ref-b.jpg", "source": "standard", "row_number": "3"},
                {"outward_code": "CODE1", "image_url": "http://example.com/ref-a.jpg", "source": "standard", "row_number": "2"},
                {"outward_code": "CODE1", "image_url": "http://example.com/raw-a.jpg", "source": "cutout", "row_number": "1"},
                {"outward_code": "CODE2", "image_url": "http://example.com/raw-b.jpg", "source": "cutout", "row_number": "4"},
            ])

            urls = db.first_standard_image_urls({"CODE1", "CODE2"})

        self.assertEqual(urls, {"CODE1": "http://example.com/ref-a.jpg"})

    def test_first_standard_image_urls_handles_many_products(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDb(Path(tmp) / "goods_marking.db")
            db.upsert_product_images([
                {"outward_code": f"CODE{index:04d}", "image_url": f"http://example.com/{index}-ref.jpg", "source": "standard", "row_number": "2"}
                for index in range(1000)
            ])

            urls = db.first_standard_image_urls({f"CODE{index:04d}" for index in range(1000)})

        self.assertEqual(len(urls), 1000)
        self.assertEqual(urls["CODE0000"], "http://example.com/0-ref.jpg")

    def test_image_metric_counts_support_exact_code_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDb(Path(tmp) / "goods_marking.db")
            db.upsert_product_images([
                {"outward_code": "CODE1", "image_url": "http://example.com/a.jpg", "source": "cutout", "row_number": "1"},
                {"outward_code": "CODE1", "image_url": "http://example.com/b.jpg", "source": "cutout", "row_number": "2"},
                {"outward_code": "CODE12", "image_url": "http://example.com/c.jpg", "source": "cutout", "row_number": "3"},
            ])
            db.upsert_review_statuses({
                ("CODE1", "http://example.com/a.jpg"): "合格",
                ("CODE12", "http://example.com/c.jpg"): "合格",
            })

            total_all = db.count_product_images()
            total_code = db.count_product_images("CODE1")
            qualified_all = db.count_review_status("合格")
            qualified_code = db.count_review_status("合格", "CODE1")

        self.assertEqual(total_all, 3)
        self.assertEqual(total_code, 2)
        self.assertEqual(qualified_all, 2)
        self.assertEqual(qualified_code, 1)

    def test_migrate_state_imports_progress_and_review_status_csv_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            progress_csv = root / "workflow_progress.csv"
            status_csv = root / "manual_status.csv"
            state_db = root / "goods_marking.db"
            with open(progress_csv, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=[
                    "outward_code", "assignee", "status", "total_urls", "downloaded_count",
                    "selected_count", "failed_count", "needs_review", "updated_at", "notes",
                ])
                writer.writeheader()
                writer.writerow({
                    "outward_code": "CODE1",
                    "assignee": "codex",
                    "status": "complete",
                    "total_urls": "3",
                    "downloaded_count": "3",
                    "selected_count": "2",
                    "failed_count": "0",
                    "needs_review": "no",
                    "updated_at": "2026-06-25T00:00:00+00:00",
                    "notes": "",
                })
            with open(status_csv, "w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["outward_code", "image_url", "人工标注状态"])
                writer.writeheader()
                writer.writerow({"outward_code": "CODE1", "image_url": "http://example.com/a.jpg", "人工标注状态": "合格"})

            args = build_parser().parse_args([
                "--state-db", str(state_db),
                "migrate-state",
                "--progress", str(progress_csv),
                "--status-csv", str(status_csv),
            ])
            exit_code = command_migrate_state(args)
            db = StateDb(state_db)

            self.assertEqual(exit_code, 0)
            self.assertEqual(db.read_progress()[0]["outward_code"], "CODE1")
            self.assertEqual(db.read_review_statuses({("CODE1", "http://example.com/a.jpg")}), {
                ("CODE1", "http://example.com/a.jpg"): "合格",
            })

    def test_migrate_state_imports_source_workbook_product_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "source.xlsx"
            state_db = root / "goods_marking.db"
            write_source_workbook(
                workbook,
                [
                    (1, ["outward_code", "image_url", "source"]),
                    (2, ["CODE1", "http://example.com/ref-a.jpg", "standard"]),
                    (3, ["CODE1", "http://example.com/raw-a.jpg", "cutout"]),
                ],
            )

            args = build_parser().parse_args([
                "--state-db", str(state_db),
                "migrate-state",
                "--source-workbook", str(workbook),
                "--progress", str(root / "missing_progress.csv"),
                "--status-csv", str(root / "missing_status.csv"),
            ])
            exit_code = command_migrate_state(args)
            urls = StateDb(state_db).first_standard_image_urls({"CODE1"})

        self.assertEqual(exit_code, 0)
        self.assertEqual(urls, {"CODE1": "http://example.com/ref-a.jpg"})


if __name__ == "__main__":
    unittest.main()
