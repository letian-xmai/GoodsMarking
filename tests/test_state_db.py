import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


if __name__ == "__main__":
    unittest.main()
