import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.excel_reader import ExcelRecord
from image_workflow.formal_workflow import process_formal_group


def image_bytes(value):
    path = Path(tempfile.gettempdir()) / f"formal_{value}.jpg"
    image = Image.new("RGB", (320, 320), (96, 106, 116))
    pixels = image.load()
    for y in range(320):
        for x in range(320):
            delta = (x * 7 + y * 3) % 19
            pixels[x, y] = (90 + delta, 100 + delta, 110 + delta)
    draw = ImageDraw.Draw(image)
    draw.rectangle((112, 32, 208, 288), fill=(value, value, max(0, value - 35)), outline=(20, 40, 80), width=5)
    draw.rectangle((122, 120, 198, 176), fill=(30, 86, 170), outline=(12, 35, 80), width=3)
    for offset in range(0, 52, 8):
        draw.line((128, 188 + offset, 192, 188 + offset), fill=(245, 245, 245), width=2)
    image.save(path, quality=95)
    return path.read_bytes()


def target_image_bytes(kind):
    path = Path(tempfile.gettempdir()) / f"formal_target_{kind}.jpg"
    image = Image.new("RGB", (360, 260), (105, 105, 105))
    pixels = image.load()
    for y in range(260):
        for x in range(360):
            delta = (x * 11 + y * 13) % 23
            pixels[x, y] = (96 + delta, 100 + delta, 104 + delta)
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 45, 250, 215), fill=(232, 214, 24), outline=(80, 70, 10), width=4)
    draw.ellipse((116, 80, 190, 155), fill=(170, 45, 28))
    for offset in range(0, 120, 12):
        draw.line((82 + offset, 54, 120 + offset, 208), fill=(190, 170, 18), width=2)
    if kind == "other":
        draw.rectangle((16, 18, 344, 238), fill=(12, 88, 206), outline=(4, 30, 90), width=4)
        draw.rectangle((104, 96, 256, 158), fill=(245, 245, 245))
    if kind == "two":
        draw.rectangle((255, 55, 345, 210), fill=(232, 214, 24), outline=(80, 70, 10), width=4)
        draw.ellipse((278, 100, 328, 150), fill=(170, 45, 28))
    if kind == "same_palette":
        draw.rectangle((265, 70, 345, 215), fill=(185, 35, 25), outline=(80, 20, 10), width=4)
    if kind == "phone":
        draw.rounded_rectangle((255, 18, 340, 238), radius=18, fill=(22, 24, 30), outline=(5, 5, 8), width=5)
        draw.rounded_rectangle((268, 48, 326, 200), radius=10, fill=(96, 100, 112))
        draw.rectangle((278, 88, 316, 128), fill=(120, 72, 108))
    image.save(path, quality=98)
    return path.read_bytes()


def write_model(path):
    feature_count = 4 * 4 + 15 + 2
    model = {
        "thumbnail_size": 4,
        "threshold": 0.99,
        "mean": [0.0] * feature_count,
        "std": [1.0] * feature_count,
        "weights": [-0.5, 1.0] + [0.0] * (feature_count - 1),
    }
    path.write_text(json.dumps(model), encoding="utf-8")


class FormalWorkflowTests(unittest.TestCase):
    def test_formal_group_skips_before_download_when_all_urls_are_standard(self):
        records = [
            ExcelRecord("sheet1", 1, 2, "CODE0", "http://example.com/STANDARD_front.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE0", "http://example.com/path/standard_back.jpg", "cutout"),
        ]

        def fetcher(url):
            raise AssertionError(f"standard-only group should not download {url}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "annotation_model.json"
            write_model(model)

            report = process_formal_group(
                records,
                root / "商品标注结果",
                model,
                target_count=40,
                fetcher=fetcher,
                download_workers=1,
            )
            result = root / "商品标注结果" / "CODE0"
            saved_report = json.loads((result / "selection_report.json").read_text(encoding="utf-8"))
            decisions = (result / "image_decisions.csv").read_text(encoding="utf-8-sig")

        self.assertEqual(report["selection_status"], "skipped_all_standard")
        self.assertTrue(report["download_complete"])
        self.assertEqual(report["selected_count"], 0)
        self.assertEqual(report["shortfall"], 40)
        self.assertFalse((result / "商品原始照片").exists())
        self.assertFalse((result / "最终结果").exists())
        self.assertEqual(len(saved_report["image_decisions"]), 2)
        self.assertTrue(all(item["最终结果是否包含该图片"] == "否" for item in saved_report["image_decisions"]))
        self.assertIn("跳过：整组下载链接均为standard参考图", decisions)

    def test_formal_group_writes_flat_result_and_backs_up_previous_run(self):
        records = [
            ExcelRecord("sheet1", 1, 2, "CODE1", "http://example.com/bright.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE1", "http://example.com/dark.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 4, "CODE1", "http://example.com/ref.jpg", "standard"),
        ]

        def fetcher(url):
            return image_bytes(230 if "bright" in url else 40)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "annotation_model.json"
            write_model(model)
            old = root / "商品标注结果" / "CODE1"
            old.mkdir(parents=True)
            (old / "old.txt").write_text("old", encoding="utf-8")

            report = process_formal_group(
                records,
                root / "商品标注结果",
                model,
                target_count=1,
                fetcher=fetcher,
                download_workers=1,
            )

            result = root / "商品标注结果" / "CODE1"
            original = result / "商品原始照片"
            backup = list((root / "商品标注结果" / "bak").glob("CODE1_*"))
            original_count = len(list(original.glob("*.jpg")))
            final_count = len(list((result / "最终结果").glob("*.jpg")))
            backup_old_exists = (backup[0] / "old.txt").exists()
            selected_exists = (result / "模型选中").exists()
            rejected_exists = (result / "模型排除").exists()
            review_exists = (result / "需人工复核").exists()
            final_exists = (result / "最终结果").exists()
            legacy_run_exists = (result / "模型运行结果").exists()
            scores_exists = (result / "model_scores.csv").exists()
            report_exists = (result / "selection_report.json").exists()
            verification_exists = (result / "verification_report.json").exists()
            qa_exists = (result / "qa_summary.txt").exists()
            product_data_exists = (root / "商品数据").exists()

        self.assertEqual(report["selection_status"], "complete")
        self.assertEqual(original_count, 3)
        self.assertEqual(len(backup), 1)
        self.assertTrue(backup_old_exists)
        self.assertTrue(selected_exists)
        self.assertTrue(rejected_exists)
        self.assertTrue(review_exists)
        self.assertTrue(final_exists)
        self.assertFalse(legacy_run_exists)
        self.assertEqual(final_count, 1)
        self.assertTrue(scores_exists)
        self.assertTrue(report_exists)
        self.assertTrue(verification_exists)
        self.assertTrue(qa_exists)
        self.assertFalse(product_data_exists)

    def test_formal_group_rejects_candidates_with_extra_products(self):
        records = [
            ExcelRecord("sheet1", 1, 2, "CODE2", "http://example.com/clean.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE2", "http://example.com/blue.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 4, "CODE2", "http://example.com/two.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 5, "CODE2", "http://example.com/red.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 6, "CODE2", "http://example.com/ref.jpg", "standard"),
        ]

        def fetcher(url):
            if "blue" in url:
                return target_image_bytes("other")
            if "two" in url:
                return target_image_bytes("two")
            if "red" in url:
                return target_image_bytes("same_palette")
            return target_image_bytes("clean")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "annotation_model.json"
            write_model(model)
            report = process_formal_group(
                records,
                root / "商品标注结果",
                model,
                target_count=4,
                fetcher=fetcher,
                download_workers=1,
            )
            result = root / "商品标注结果" / "CODE2"
            final_count = len(list((result / "最终结果").glob("*.jpg")))
            rejected_count = len(list((result / "模型排除").glob("*.jpg")))
            scores = (result / "model_scores.csv").read_text(encoding="utf-8-sig")

        self.assertEqual(report["selection_status"], "shortfall")
        self.assertEqual(final_count, 1)
        self.assertGreaterEqual(rejected_count, 1)
        self.assertIn("has_other_product_color", scores)
        self.assertIn("has_multiple_target_instances", scores)

    def test_formal_group_rejects_candidates_with_phone(self):
        records = [
            ExcelRecord("sheet1", 1, 2, "CODE3", "http://example.com/clean.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 3, "CODE3", "http://example.com/phone.jpg", "cutout"),
            ExcelRecord("sheet1", 1, 4, "CODE3", "http://example.com/ref.jpg", "standard"),
        ]

        def fetcher(url):
            return target_image_bytes("phone" if "phone" in url else "clean")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "annotation_model.json"
            write_model(model)
            report = process_formal_group(
                records,
                root / "商品标注结果",
                model,
                target_count=2,
                fetcher=fetcher,
                download_workers=1,
            )
            result = root / "商品标注结果" / "CODE3"
            final_count = len(list((result / "最终结果").glob("*.jpg")))
            scores = (result / "model_scores.csv").read_text(encoding="utf-8-sig")

        self.assertEqual(report["selection_status"], "shortfall")
        self.assertEqual(final_count, 1)
        self.assertIn("has_phone_like_object", scores)


if __name__ == "__main__":
    unittest.main()
