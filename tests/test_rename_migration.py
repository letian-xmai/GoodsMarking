import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.rename_migration import migrate_workspace


class RenameMigrationTests(unittest.TestCase):
    def test_migrates_formal_outputs_and_training_cache_to_url_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product = root / "商品标注结果" / "CODE1"
            for folder in ("商品原始照片", "模型选中", "模型排除", "需人工复核", "最终结果"):
                (product / folder).mkdir(parents=True)
            old_source = "r000002__cutout__aaaaaaaaaaaa.jpg"
            new_source = "url_name.jpg"
            old_final = "01_front_label__001__r000002__cutout__aaaaaaaaaaaa.jpg"
            (product / "商品原始照片" / old_source).write_bytes(b"image")
            (product / "模型选中" / old_source).write_bytes(b"image")
            (product / "最终结果" / old_final).write_bytes(b"image")
            _write_csv(product / "manifest.csv", ["row_number", "url", "source", "status", "filename", "error"], [
                {"row_number": "2", "url": "http://example.com/path/url_name.jpg?x=1", "source": "cutout", "status": "downloaded", "filename": old_source, "error": ""}
            ])
            shutil_manifest = product / "商品原始照片" / "manifest.csv"
            _write_csv(shutil_manifest, ["row_number", "url", "source", "status", "filename", "error"], [
                {"row_number": "2", "url": "http://example.com/path/url_name.jpg?x=1", "source": "cutout", "status": "downloaded", "filename": old_source, "error": ""}
            ])
            _write_csv(product / "model_scores.csv", ["source_name", "model_score", "prediction", "bucket", "selected_final", "result_filename"], [
                {"source_name": old_source, "model_score": "0.8", "prediction": "1", "bucket": "模型选中", "selected_final": "True", "result_filename": old_final}
            ])
            (product / "selection_report.json").write_text(json.dumps({
                "selected": [{"source_name": old_source, "result_filename": old_final}]
            }), encoding="utf-8")

            train = root / "模型训练数据"
            old_cache = train / "images" / "train" / "CODE2" / "CODE2__abc.jpg"
            old_role = train / "商品数据" / "CODE2" / "商品原始照片" / "CODE2__abc.jpg"
            old_cache.parent.mkdir(parents=True)
            old_role.parent.mkdir(parents=True)
            old_cache.write_bytes(b"image")
            old_role.write_bytes(b"image")
            row = {
                "image_url": "http://example.com/cache/cache_name.jpg",
                "image_path": str(old_cache.relative_to(root)),
                "quality_metrics": {"path": str(old_cache.relative_to(root))},
                "original_image_path": str(old_role.relative_to(root)),
                "annotation_result_path": None,
            }
            (train / "manifest_all.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

            stats = migrate_workspace(root, dry_run=False)

            new_final = "url_name.jpg"
            self.assertGreaterEqual(stats.renamed_files, 5)
            self.assertTrue((product / "商品原始照片" / new_source).exists())
            self.assertTrue((product / "模型选中" / new_source).exists())
            self.assertTrue((product / "最终结果" / new_final).exists())
            self.assertFalse((product / "商品原始照片" / old_source).exists())
            self.assertIn(new_source, (product / "model_scores.csv").read_text(encoding="utf-8-sig"))
            self.assertIn(new_final, (product / "selection_report.json").read_text(encoding="utf-8"))
            self.assertTrue((train / "images" / "train" / "CODE2" / "cache_name.jpg").exists())
            self.assertTrue((train / "商品数据" / "CODE2" / "商品原始照片" / "cache_name.jpg").exists())
            self.assertIn("cache_name.jpg", (train / "manifest_all.jsonl").read_text(encoding="utf-8"))


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
