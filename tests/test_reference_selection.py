import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.selection import select_downloaded_group


def make_product_image(path):
    image = Image.new("RGB", (120, 160), (210, 230, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 20, 95, 140), outline=(0, 0, 0), width=3)
    draw.text((35, 70), "OK", fill=(0, 0, 0))
    image.save(path, quality=95)


def make_standard_reference_image(path):
    image = Image.new("RGB", (240, 240), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((92, 58, 148, 198), fill=(210, 220, 230), outline=(80, 80, 80), width=2)
    draw.rectangle((100, 118, 140, 142), fill=(20, 80, 180))
    image.save(path, quality=95)


class ReferenceSelectionTests(unittest.TestCase):
    def test_standard_white_background_images_are_reference_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE6"
            result = Path(tmp) / "result" / "CODE6"
            original.mkdir(parents=True)
            make_standard_reference_image(original / "r000010__standard__abc.jpg")
            make_product_image(original / "r000011__cutout__def.jpg")

            report = select_downloaded_group("CODE6", original, result, target_count=1)
            reference_files = sorted((result / "reference").glob("*.jpg"))

        self.assertEqual(report["reference_count"], 1)
        self.assertEqual(report["white_reference_count"], 1)
        self.assertIn("standard", reference_files[0].name)
        self.assertFalse(any("standard" in item["result_filename"] for item in report["selected"]))


if __name__ == "__main__":
    unittest.main()
