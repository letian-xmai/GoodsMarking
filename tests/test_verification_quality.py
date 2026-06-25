import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.verification import verify_group


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


class VerificationQualityTests(unittest.TestCase):
    def test_verify_flags_repeated_product_parts_in_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "original" / "CODE14"
            result = Path(tmp) / "result" / "CODE14"
            original.mkdir(parents=True)
            result.mkdir(parents=True)
            make_repeated_product_parts(original / "source.jpg")
            make_repeated_product_parts(result / "01_front_label__001__source.jpg")

            verified = verify_group("CODE14", original, result, expected_original_count=1, target_count=1)

        self.assertFalse(verified["ok"])
        self.assertIn("result_repeated_product_parts:01_front_label__001__source.jpg", verified["issues"])


if __name__ == "__main__":
    unittest.main()
