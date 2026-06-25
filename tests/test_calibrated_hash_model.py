import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.calibrated_hash_model import build_calibrated_hash_model
from image_workflow.cli import build_parser
from image_workflow.full_evaluation import evaluate_full_testset
from image_workflow.model_run_outputs import default_run_id


def make_row(sample_id, split, label, phash):
    return {
        "sample_id": sample_id,
        "outward_code": sample_id.split("_")[0],
        "image_url": f"http://example.com/{sample_id}.jpg",
        "image_path": f"images/{sample_id}.jpg",
        "label": label,
        "split": split,
        "quality_metrics": {"perceptual_hash": phash},
    }


class CalibratedHashModelTests(unittest.TestCase):
    def test_calibrated_hash_model_replays_majority_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                make_row("A_1", "train", 1, "1111"),
                make_row("A_2", "test", 1, "1111"),
                make_row("B_1", "test", 0, "0000"),
                make_row("B_2", "val", 0, "0000"),
            ]
            with open(root / "manifest_all.jsonl", "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")

            report = build_calibrated_hash_model(root, root / "model")
            predictions = [json.loads(line) for line in (root / "model" / "calibrated_test_predictions.jsonl").read_text().splitlines()]

        self.assertEqual(report["splits"]["test"]["accuracy"], 1.0)
        self.assertEqual([row["prediction"] for row in predictions], [1, 0])
        self.assertTrue(report["calibration_warning"])

    def test_evaluate_full_testset_writes_mismatch_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "model"
            model.mkdir()
            (root / "images").mkdir()
            rows = [
                make_row("A_1", "train", 1, "1111"),
                make_row("A_2", "train", 0, "1111"),
                make_row("B_1", "test", 0, "0000"),
            ]
            for row in rows:
                (root / row["image_path"]).write_bytes(b"image")
            legacy_run = root / "模型运行结果" / "old" / "模型选中" / "A"
            legacy_run.mkdir(parents=True)
            (legacy_run / "old.jpg").write_bytes(b"image")
            with open(root / "manifest_all.jsonl", "w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row) + "\n")
            (model / "calibrated_hash_model.json").write_text(
                json.dumps({"hash_labels": {"1111": {"label": 1, "positive": 2, "negative": 1, "total": 3}, "0000": {"label": 0, "positive": 0, "negative": 1, "total": 1}}}),
                encoding="utf-8",
            )

            report = evaluate_full_testset(root, model, write_preview=False, run_id="unit")
            mismatches = (model / "full_testset_mismatches.csv").read_text(encoding="utf-8-sig")
            summary = (model / "full_testset_match_report.md").read_text(encoding="utf-8")
            run_root = root / "商品数据"
            model_selected_exists = (run_root / "模型选中" / "A" / "A_1.jpg").exists()
            product_selected_exists = (run_root / "A" / "模型运行结果" / "unit" / "模型选中" / "A_1.jpg").exists()
            model_rejected_exists = (run_root / "B" / "模型运行结果" / "unit" / "模型排除" / "B_1.jpg").exists()
            mismatch_exists = (run_root / "A" / "模型运行结果" / "unit" / "不匹配" / "A_2.jpg").exists()
            split_dir_exists = (run_root / "A" / "模型运行结果" / "unit" / "模型选中" / "train").exists()
            legacy_run_exists = (root / "模型运行结果").exists()
            archived_legacy = list((root / "_legacy").rglob("old.jpg"))

        self.assertEqual(report["samples"], 3)
        self.assertEqual(report["mismatches"], 1)
        self.assertIn("A_2", mismatches)
        self.assertIn("同一感知哈希簇存在人工正负标签冲突", mismatches)
        self.assertIn("人工匹配率", summary)
        self.assertFalse(model_selected_exists)
        self.assertTrue(product_selected_exists)
        self.assertTrue(model_rejected_exists)
        self.assertTrue(mismatch_exists)
        self.assertFalse(split_dir_exists)
        self.assertFalse(legacy_run_exists)
        self.assertEqual(len(archived_legacy), 1)

    def test_default_run_id_uses_timestamp(self):
        run_id = default_run_id()

        self.assertRegex(run_id, r"^\d{8}_\d{6}$")

    def test_cli_accepts_evaluate_full_testset_command(self):
        args = build_parser().parse_args(
            ["evaluate-full-testset", "--dataset-dir", "模型训练数据", "--model-dir", "模型训练数据/model", "--run-id", "manual", "--no-preview"]
        )

        self.assertEqual(args.command, "evaluate-full-testset")
        self.assertEqual(args.dataset_dir, "模型训练数据")
        self.assertEqual(args.model_dir, "模型训练数据/model")
        self.assertEqual(args.run_id, "manual")
        self.assertFalse(args.preview)


if __name__ == "__main__":
    unittest.main()
