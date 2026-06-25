import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.formal_workflow import HARD_FLAGS
from image_workflow.phone_detection import _looks_like_phone
from image_workflow.quality import analyze_image
from image_workflow.target_consistency import _has_low_target_occupancy, _has_multiple_target_instances, _has_other_product_color


class FormalRuleTuningTests(unittest.TestCase):
    def test_low_resolution_is_not_formal_hard_exclusion(self):
        self.assertNotIn("is_low_resolution", HARD_FLAGS)

    def test_phone_detector_does_not_reject_dark_cylindrical_product(self):
        cylinder_like = {
            "area_ratio": 0.108,
            "bbox_ratio": 0.400,
            "fill_ratio": 0.270,
            "elongation": 2.05,
        }
        phone_like = {
            "area_ratio": 0.159,
            "bbox_ratio": 0.475,
            "fill_ratio": 0.335,
            "elongation": 2.86,
        }

        self.assertFalse(_looks_like_phone(cylinder_like))
        self.assertTrue(_looks_like_phone(phone_like))

    def test_label_color_blocks_do_not_count_as_multiple_targets(self):
        self.assertFalse(_has_multiple_target_instances(3, 0.037, 0.466))
        self.assertFalse(_has_multiple_target_instances(4, 0.062, 0.016))
        self.assertTrue(_has_multiple_target_instances(2, 0.080, 0.020))

    def test_moderate_background_color_is_not_other_product(self):
        self.assertFalse(_has_other_product_color(0.162, 0.115))
        self.assertTrue(_has_other_product_color(0.635, 0.600))

    def test_dominant_non_target_area_counts_as_low_target_occupancy(self):
        self.assertTrue(_has_low_target_occupancy(0.055, 0.481))
        self.assertTrue(_has_low_target_occupancy(0.093, 0.384))
        self.assertFalse(_has_low_target_occupancy(0.053, 0.209))
        self.assertFalse(_has_low_target_occupancy(0.049, 0.273))

    def test_decimal_10kb_file_size_is_not_low_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "near_10kb.jpg"
            Image.new("RGB", (120, 140), (180, 190, 200)).save(path, quality=80)
            if path.stat().st_size < 10050:
                with path.open("ab") as handle:
                    handle.write(b"0" * (10050 - path.stat().st_size))

            metrics = analyze_image(path)

        self.assertGreaterEqual(metrics.file_size_bytes, 10000)
        self.assertFalse(metrics.is_low_file_size)


if __name__ == "__main__":
    unittest.main()
