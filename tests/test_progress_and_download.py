import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.downloader import download_group
from image_workflow.excel_reader import ExcelRecord
from image_workflow.progress import ProgressTable


class ProgressTableTests(unittest.TestCase):
    def test_progress_table_records_assignment_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = ProgressTable(Path(tmp) / "workflow_progress.csv")
            table.upsert(
                outward_code="CODE3",
                assignee="codex",
                status="selected",
                total_urls=42,
                downloaded_count=42,
                selected_count=40,
                failed_count=0,
                needs_review=True,
                notes="angle weak labels",
            )
            rows = table.read_all()

        self.assertEqual(rows[0]["outward_code"], "CODE3")
        self.assertEqual(rows[0]["assignee"], "codex")
        self.assertEqual(rows[0]["status"], "selected")
        self.assertEqual(rows[0]["selected_count"], "40")
        self.assertEqual(rows[0]["needs_review"], "yes")

    def test_progress_table_syncs_rows_to_sqlite_state_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            progress_csv = Path(tmp) / "workflow_progress.csv"
            state_db = Path(tmp) / "goods_marking.db"
            table = ProgressTable(progress_csv, state_db)
            table.upsert(outward_code="CODE3", status="selected", total_urls=42, selected_count=40)

            rows = ProgressTable(progress_csv, state_db).read_all()

        self.assertEqual(rows[0]["outward_code"], "CODE3")
        self.assertEqual(rows[0]["status"], "selected")
        self.assertEqual(rows[0]["selected_count"], "40")


class DownloadTests(unittest.TestCase):
    def test_download_group_writes_manifest_and_skips_existing_files(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            return b"image-bytes"

        records = [
            ExcelRecord("sheet1", 1, 2, "CODE4", "http://example.com/a.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE4", "http://example.com/b.jpg", "standard"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "CODE4"
            first = download_group(records, original, fetcher=fake_fetch, workers=1)
            second = download_group(records, original, fetcher=fake_fetch, workers=1)
            image_files = sorted(path.name for path in original.glob("*.jpg"))
            manifest = (original / "manifest.csv").read_text(encoding="utf-8")

        self.assertEqual(first["downloaded_count"], 2)
        self.assertEqual(second["downloaded_count"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(image_files), 2)
        self.assertEqual(image_files, ["a.jpg", "b.jpg"])
        self.assertIn("downloaded", manifest)

    def test_download_group_appends_underscore_identifier_for_duplicate_url_names(self):
        def fake_fetch(url):
            return f"bytes:{url}".encode()

        records = [
            ExcelRecord("sheet1", 1, 2, "CODE5", "http://example.com/a/same.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE5", "http://example.com/b/same.jpg", "cutout"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "CODE5"
            download_group(records, original, fetcher=fake_fetch, workers=1)
            image_files = sorted(path.name for path in original.glob("*.jpg"))
            manifest = (original / "manifest.csv").read_text(encoding="utf-8")

        self.assertEqual(len(image_files), 2)
        self.assertIn("same.jpg", image_files)
        self.assertTrue(any(name.startswith("same_") and name.endswith(".jpg") for name in image_files))
        self.assertIn("http://example.com/a/same.jpg", manifest)
        self.assertIn("http://example.com/b/same.jpg", manifest)


if __name__ == "__main__":
    unittest.main()
