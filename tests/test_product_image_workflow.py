import csv
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.excel_reader import inspect_workbook, iter_excel_records
from image_workflow.naming import build_original_filename, build_result_filename
from image_workflow.selection import analyze_image, select_downloaded_group
from image_workflow.verification import verify_group


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def cell(ref, value):
    return (
        f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
    )


def sheet_xml(rows):
    body = []
    for row_number, values in rows:
        cells = "".join(cell(f"{chr(65 + idx)}{row_number}", value) for idx, value in enumerate(values))
        body.append(f'<row r="{row_number}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS}"><dimension ref="A1"/>'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    ).encode()


def write_fake_xlsx(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            sheet_xml(
                [
                    (1, ["outward_code", "image_url", "source"]),
                    (2, ["A001", "http://example.com/a.jpg", "cutout"]),
                    (3, ["A001", "http://example.com/b.jpg", "standard"]),
                ]
            ),
        )
        zf.writestr(
            "xl/worksheets/sheet2.xml",
            sheet_xml(
                [
                    (1, ["B002", "http://example.com/c.jpg", "cutout"]),
                    (2, ["B002", "http://example.com/d.jpg", "cutout"]),
                ]
            ),
        )


def make_product_image(path, color, label):
    image = Image.new("RGB", (420, 420), color)
    draw = ImageDraw.Draw(image)
    draw.rectangle((140, 45, 280, 380), fill=color, outline=(0, 0, 0), width=4)
    draw.rectangle((154, 180, 266, 235), fill=(20, 80, 180))
    draw.text((170, 250), label, fill=(0, 0, 0))
    for x in range(145, 280, 8):
        draw.line((x, 50, x + 25, 375), fill=(175, 205, 225), width=1)
    image.save(path, quality=98)


def make_cropped_product_image(path):
    image = Image.new("RGB", (160, 160), (230, 230, 230))
    draw = ImageDraw.Draw(image)
    draw.rectangle((-30, -30, 150, 120), fill=(190, 210, 230), outline=(0, 0, 0), width=6)
    image.save(path, quality=95)


class ExcelReaderTests(unittest.TestCase):
    def test_iter_excel_records_handles_bad_dimension_and_headerless_followup_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "input.xlsx"
            write_fake_xlsx(xlsx)

            records = list(iter_excel_records(xlsx))
            summary = inspect_workbook(xlsx)

        self.assertEqual([record.outward_code for record in records], ["A001", "A001", "B002", "B002"])
        self.assertEqual(records[0].row_number, 2)
        self.assertEqual(records[2].row_number, 1)
        self.assertEqual(summary.total_urls, 4)
        self.assertEqual(summary.group_counts["A001"], 2)
        self.assertEqual(summary.group_counts["B002"], 2)


class NamingTests(unittest.TestCase):
    def test_original_filename_matches_url_basename(self):
        url = "http://example.com/path/tagmark%2Fframe_188_1782217054_0478b5.jpeg?x=1"

        filename = build_original_filename(12, url, "cutout")

        self.assertEqual(filename, "frame_188_1782217054_0478b5.jpeg")

    def test_result_filename_keeps_source_name(self):
        self.assertEqual(build_result_filename(1, "front_label", 1, "url_name.png"), "url_name.png")


class SelectionTests(unittest.TestCase):
    def test_selection_excludes_white_background_and_copies_without_moving_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "original" / "CODE1"
            result = root / "result" / "CODE1"
            original.mkdir(parents=True)

            Image.new("RGB", (120, 160), (255, 255, 255)).save(original / "white.jpg")
            make_product_image(original / "front.jpg", (210, 230, 245), "F")
            make_product_image(original / "back.jpg", (230, 220, 205), "B")
            make_product_image(original / "top.jpg", (205, 235, 215), "T")
            make_product_image(original / "side.jpg", (235, 205, 225), "S")

            report = select_downloaded_group("CODE1", original, result, target_count=3)
            verified = verify_group("CODE1", original, result, expected_original_count=5, target_count=3)

            source_images = sorted(p.name for p in original.glob("*.jpg"))
            result_images = sorted(p for p in result.glob("*.jpg"))
            report_exists = (result / "selection_report.json").exists()
            with open(result / "selection_report.json", "r", encoding="utf-8") as fh:
                saved_report = json.load(fh)

        self.assertEqual(len(source_images), 5)
        self.assertEqual(len(result_images), 3)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(verified["ok"])
        self.assertTrue(report_exists)
        self.assertEqual(saved_report["selected_count"], 3)
        self.assertFalse(any(item["is_white_background"] for item in saved_report["selected"]))

    def test_analyze_image_marks_plain_white_as_white_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "plain.jpg"
            Image.new("RGB", (80, 80), (255, 255, 255)).save(image_path)

            metrics = analyze_image(image_path)

        self.assertTrue(metrics.is_white_background)
        self.assertLess(metrics.quality_score, 0)

    def test_selection_penalizes_edge_cropped_partial_products(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE5"
            result = Path(tmp) / "result" / "CODE5"
            original.mkdir(parents=True)
            make_cropped_product_image(original / "cropped.jpg")
            make_product_image(original / "complete.jpg", (210, 230, 245), "OK")

            cropped = analyze_image(original / "cropped.jpg")
            report = select_downloaded_group("CODE5", original, result, target_count=1)

        self.assertTrue(cropped.is_edge_cropped)
        self.assertEqual(report["selected"][0]["result_filename"], "complete.jpg")

    def test_analyze_image_marks_narrow_partial_closeups_as_edge_cropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "narrow.jpg"
            image = Image.new("RGB", (113, 187), (220, 230, 240))
            ImageDraw.Draw(image).rectangle((0, 0, 112, 130), fill=(180, 205, 225))
            image.save(image_path)

            metrics = analyze_image(image_path)

        self.assertTrue(metrics.is_edge_cropped)


class ManifestTests(unittest.TestCase):
    def test_verify_group_reads_manifest_expected_count_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE2"
            result = Path(tmp) / "result" / "CODE2"
            original.mkdir(parents=True)
            result.mkdir(parents=True)
            make_product_image(original / "one.jpg", (210, 230, 245), "1")
            make_product_image(result / "01_front_label__001__one.jpg", (210, 230, 245), "1")
            with open(original / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["row_number", "url", "status", "filename"])
                writer.writeheader()
                writer.writerow({"row_number": "1", "url": "http://example.com/1.jpg", "status": "downloaded", "filename": "one.jpg"})

            verified = verify_group("CODE2", original, result, target_count=1)

        self.assertTrue(verified["ok"])
        self.assertEqual(verified["expected_original_count"], 1)


if __name__ == "__main__":
    unittest.main()
