import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.annotation_model import train_annotation_model


def make_image(path, value):
    Image.new("RGB", (32, 32), (value, value, value)).save(path, quality=95)


def row(root, split, idx, label, value):
    path = root / f"{split}_{idx}.jpg"
    make_image(path, value)
    return {
        "sample_id": f"{split}_{idx}",
        "outward_code": f"G{idx}",
        "image_url": f"http://example.com/{split}_{idx}.jpg",
        "image_path": str(path),
        "label": label,
        "split": split,
        "source_types": ["选中"] if label else ["备选"],
        "row_numbers": [idx],
        "download_status": "downloaded",
        "quality_metrics": {
            "width": 32,
            "height": 32,
            "brightness": value,
            "white_ratio": 0.0,
            "edge_score": value / 20,
            "subject_ratio": label,
            "quality_score": value,
            "is_white_background": False,
            "is_blurry": False,
            "is_tiny_subject": False,
            "is_edge_cropped": False,
            "file_size_bytes": path.stat().st_size,
            "foreground_contrast": value,
            "foreground_component_count": 1,
            "second_component_ratio": 0.0,
            "is_low_file_size": False,
            "has_multiple_products": False,
            "is_low_boundary_contrast": False,
            "is_incomplete_product": False,
            "has_repeated_product_parts": False,
        },
    }


class AnnotationModelTests(unittest.TestCase):
    def test_train_annotation_model_writes_report_and_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                row(root, "train", 1, 0, 40),
                row(root, "train", 2, 1, 220),
                row(root, "val", 3, 0, 50),
                row(root, "val", 4, 1, 210),
                row(root, "test", 5, 0, 60),
                row(root, "test", 6, 1, 200),
            ]
            with open(root / "manifest_all.jsonl", "w", encoding="utf-8") as handle:
                for item in rows:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")

            report = train_annotation_model(root, root / "model", epochs=300, thumbnail_size=4)

            saved_report = json.loads((root / "model" / "evaluation_report.json").read_text())
            predictions = (root / "model" / "test_predictions.jsonl").read_text().splitlines()
            model_exists = (root / "model" / "annotation_model.json").exists()

        self.assertGreaterEqual(report["splits"]["test"]["accuracy"], 0.95)
        self.assertGreaterEqual(report["confidence_gate"]["splits"]["test"]["accuracy"], 0.95)
        self.assertEqual(saved_report["splits"]["test"]["accuracy"], report["splits"]["test"]["accuracy"])
        self.assertEqual(saved_report["confidence_gate"]["splits"]["test"]["accuracy"], report["confidence_gate"]["splits"]["test"]["accuracy"])
        self.assertEqual(len(predictions), 2)
        self.assertTrue(model_exists)


if __name__ == "__main__":
    unittest.main()
