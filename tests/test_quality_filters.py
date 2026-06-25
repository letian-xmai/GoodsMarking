import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.selection import analyze_image, select_downloaded_group


def make_single_product(path):
    image = Image.new("RGB", (420, 420), (210, 225, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((140, 45, 280, 380), fill=(190, 215, 235), outline=(0, 0, 0), width=4)
    draw.rectangle((154, 180, 266, 235), fill=(20, 80, 180))
    for x in range(145, 280, 8):
        draw.line((x, 50, x + 25, 375), fill=(175, 205, 225), width=1)
    image.save(path, quality=98)


def make_two_product_image(path):
    image = Image.new("RGB", (420, 260), (225, 225, 225))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 25, 170, 235), fill=(190, 215, 235), outline=(0, 0, 0), width=4)
    draw.rectangle((250, 25, 380, 235), fill=(190, 215, 235), outline=(0, 0, 0), width=4)
    for y in range(35, 235, 8):
        draw.line((45, y, 165, y + 2), fill=(175, 205, 225), width=1)
        draw.line((255, y, 375, y + 2), fill=(175, 205, 225), width=1)
    image.save(path, quality=98)


def make_low_contrast_image(path):
    image = Image.new("RGB", (420, 420), (216, 224, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((130, 45, 290, 380), fill=(205, 216, 228), outline=(198, 210, 222), width=3)
    for x in range(135, 290, 7):
        draw.line((x, 50, x + 20, 375), fill=(200, 212, 224), width=1)
    image.save(path, quality=98)


def make_edge_fill_image(path):
    image = Image.new("RGB", (119, 420), (210, 225, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 30, 111, 390), fill=(190, 215, 235), outline=(0, 0, 0), width=5)
    draw.rectangle((22, 180, 98, 245), fill=(20, 80, 180))
    for y in range(35, 390, 7):
        draw.line((10, y, 110, y + 3), fill=(175, 205, 225), width=1)
    image.save(path, quality=98)


def make_incomplete_closeup(path):
    image = Image.new("RGB", (260, 260), (110, 120, 130))
    draw = ImageDraw.Draw(image)
    draw.rectangle((5, 5, 255, 255), fill=(155, 165, 175))
    draw.rectangle((60, 60, 205, 170), fill=(130, 175, 190))
    for x in range(10, 255, 11):
        draw.line((x, 8, x + 22, 252), fill=(125, 135, 145), width=1)
    image.save(path, quality=98)


def make_repeated_product_parts(path):
    image = Image.new("RGB", (119, 260), (110, 118, 128))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((42, 5, 114, 250), radius=18, fill=(155, 165, 175), outline=(100, 108, 118), width=3)
    draw.rectangle((55, 95, 105, 135), fill=(70, 115, 170))
    draw.ellipse((2, 160, 38, 198), fill=(170, 172, 170), outline=(100, 105, 110), width=3)
    draw.ellipse((8, 210, 42, 248), fill=(170, 172, 170), outline=(100, 105, 110), width=3)
    for y in range(8, 250, 7):
        draw.line((44, y, 112, y + 3), fill=(135, 145, 155), width=1)
    image.save(path, quality=98)


def make_underexposed_image(path):
    image = Image.new("RGB", (320, 260), (38, 38, 42))
    pixels = image.load()
    for y in range(260):
        for x in range(320):
            delta = (x * 9 + y * 7) % 17
            pixels[x, y] = (32 + delta, 32 + delta, 36 + delta)
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 45, 250, 215), fill=(48, 36, 40), outline=(75, 75, 80), width=4)
    draw.rectangle((105, 110, 210, 150), fill=(65, 28, 35))
    image.save(path, quality=98)


def make_low_resolution_product(path):
    image = Image.new("RGB", (145, 170), (210, 225, 240))
    pixels = image.load()
    for y in range(170):
        for x in range(145):
            delta = (x * 13 + y * 5) % 29
            pixels[x, y] = (196 + delta, 206 + delta, 216 + delta)
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 20, 112, 150), fill=(185, 210, 230), outline=(0, 0, 0), width=3)
    draw.rectangle((48, 70, 100, 96), fill=(20, 80, 180))
    image.save(path, quality=100)


class QualityFilterTests(unittest.TestCase):
    def test_images_smaller_than_10kb_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE7"
            result = Path(tmp) / "result" / "CODE7"
            original.mkdir(parents=True)
            Image.new("RGB", (80, 80), (210, 220, 230)).save(original / "tiny.jpg", quality=45)
            make_single_product(original / "complete.jpg")

            tiny = analyze_image(original / "tiny.jpg")
            report = select_downloaded_group("CODE7", original, result, target_count=1)

        self.assertTrue(tiny.is_low_file_size)
        self.assertIn("complete", report["selected"][0]["result_filename"])

    def test_images_with_two_products_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE8"
            result = Path(tmp) / "result" / "CODE8"
            original.mkdir(parents=True)
            make_two_product_image(original / "two_products.jpg")
            make_single_product(original / "one_product.jpg")

            multi = analyze_image(original / "two_products.jpg")
            report = select_downloaded_group("CODE8", original, result, target_count=1)

        self.assertTrue(multi.has_multiple_products)
        self.assertIn("one_product", report["selected"][0]["result_filename"])

    def test_low_product_background_contrast_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE9"
            result = Path(tmp) / "result" / "CODE9"
            original.mkdir(parents=True)
            make_low_contrast_image(original / "low_contrast.jpg")
            make_single_product(original / "clear_product.jpg")

            low = analyze_image(original / "low_contrast.jpg")
            report = select_downloaded_group("CODE9", original, result, target_count=1)

        self.assertTrue(low.is_low_boundary_contrast)
        self.assertIn("clear_product", report["selected"][0]["result_filename"])

    def test_edge_cropped_images_can_fill_after_strict_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE10"
            result = Path(tmp) / "result" / "CODE10"
            original.mkdir(parents=True)
            make_single_product(original / "strict_product.jpg")
            make_edge_fill_image(original / "edge_fill.jpg")

            report = select_downloaded_group("CODE10", original, result, target_count=2)

        self.assertEqual(report["selected_count"], 2)
        self.assertEqual(report["strict_candidate_count"], 1)
        self.assertEqual(report["fill_candidate_count"], 1)
        self.assertEqual(report["selected"][1]["selection_tier"], "fill")
        self.assertEqual(report["selected"][1]["fill_reason"], "edge_cropped")

    def test_incomplete_closeups_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE12"
            result = Path(tmp) / "result" / "CODE12"
            original.mkdir(parents=True)
            make_incomplete_closeup(original / "incomplete.jpg")
            make_single_product(original / "complete.jpg")

            incomplete = analyze_image(original / "incomplete.jpg")
            report = select_downloaded_group("CODE12", original, result, target_count=1)

        self.assertTrue(incomplete.is_incomplete_product)
        self.assertFalse(incomplete.is_low_file_size)
        self.assertFalse(incomplete.is_low_boundary_contrast)
        self.assertIn("complete", report["selected"][0]["result_filename"])

    def test_repeated_product_parts_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE13"
            result = Path(tmp) / "result" / "CODE13"
            original.mkdir(parents=True)
            make_repeated_product_parts(original / "repeated.jpg")
            make_single_product(original / "complete.jpg")

            repeated = analyze_image(original / "repeated.jpg")
            report = select_downloaded_group("CODE13", original, result, target_count=2)

        self.assertTrue(repeated.has_repeated_product_parts)
        self.assertEqual(report["selected_count"], 1)
        self.assertIn("complete", report["selected"][0]["result_filename"])

    def test_underexposed_images_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE14"
            result = Path(tmp) / "result" / "CODE14"
            original.mkdir(parents=True)
            make_underexposed_image(original / "dark.jpg")
            make_single_product(original / "complete.jpg")

            dark = analyze_image(original / "dark.jpg")
            report = select_downloaded_group("CODE14", original, result, target_count=1)

        self.assertTrue(dark.is_underexposed)
        self.assertFalse(dark.is_low_file_size)
        self.assertIn("complete", report["selected"][0]["result_filename"])

    def test_low_resolution_images_are_excluded_even_when_over_10kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE15"
            result = Path(tmp) / "result" / "CODE15"
            original.mkdir(parents=True)
            make_low_resolution_product(original / "low_resolution.jpg")
            make_single_product(original / "complete.jpg")

            low_res = analyze_image(original / "low_resolution.jpg")
            report = select_downloaded_group("CODE15", original, result, target_count=1)

        self.assertTrue(low_res.is_low_resolution)
        self.assertFalse(low_res.is_low_file_size)
        self.assertIn("complete", report["selected"][0]["result_filename"])

    def test_report_contains_completed_quality_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE11"
            result = Path(tmp) / "result" / "CODE11"
            original.mkdir(parents=True)
            make_single_product(original / "strict_product.jpg")

            report = select_downloaded_group("CODE11", original, result, target_count=1)

        self.assertEqual(report["quality_rules"]["min_file_size_bytes"], 10000)
        self.assertEqual(report["quality_rules"]["standard_images_role"], "reference_only")
        self.assertTrue(report["quality_rules"]["single_product_required"])


if __name__ == "__main__":
    unittest.main()
