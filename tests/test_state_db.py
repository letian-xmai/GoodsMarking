import sys
import tempfile
import unittest
from pathlib import Path
import csv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.cli import build_parser, command_migrate_state
from image_workflow.state_db import StateDb


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


if __name__ == "__main__":
    unittest.main()
