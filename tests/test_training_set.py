import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from image_workflow.training_set import (
    assign_group_splits,
    build_labeled_samples,
    build_training_dataset,
    parse_label_records,
)
from image_workflow.cli import build_parser


NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def cell(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def write_label_xlsx(path):
    rows = [
        (1, ["outward_code", "image_url", "type"]),
        (2, ["A001", "http://example.com/a.jpg", "备选"]),
        (3, ["A001", "http://example.com/a.jpg", "选中"]),
        (4, ["A001", "http://example.com/b.jpg", "备选"]),
        (5, ["B002", "http://example.com/c.jpg", "选中"]),
        (6, ["C003", "http://example.com/d.jpg", "备选"]),
    ]
    body = []
    for row_number, values in rows:
        cells = "".join(cell(f"{chr(65 + idx)}{row_number}", value) for idx, value in enumerate(values))
        body.append(f'<row r="{row_number}">{cells}</row>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{NS}"><dimension ref="A1"/>'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", xml)


def jpg_bytes():
    with tempfile.NamedTemporaryFile(suffix=".jpg") as handle:
        Image.new("RGB", (140, 160), (180, 205, 225)).save(handle.name, quality=95)
        return Path(handle.name).read_bytes()


class TrainingSetTests(unittest.TestCase):
    def test_parse_label_records_ignores_bad_dimension(self):
        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "labels.xlsx"
            write_label_xlsx(workbook)

            records = list(parse_label_records(workbook))

        self.assertEqual(len(records), 5)
        self.assertEqual(records[0].row_number, 2)
        self.assertEqual(records[0].type_value, "备选")
        self.assertEqual(records[1].type_value, "选中")

    def test_build_labeled_samples_prefers_selected_over_candidate(self):
        records = [
            type("R", (), {"row_number": 2, "outward_code": "A", "image_url": "u1", "type_value": "备选"}),
            type("R", (), {"row_number": 3, "outward_code": "A", "image_url": "u1", "type_value": "选中"}),
            type("R", (), {"row_number": 4, "outward_code": "A", "image_url": "u2", "type_value": "备选"}),
        ]

        samples = build_labeled_samples(records)

        by_url = {item.image_url: item for item in samples}
        self.assertEqual(by_url["u1"].label, 1)
        self.assertEqual(by_url["u1"].source_types, ["备选", "选中"])
        self.assertEqual(by_url["u2"].label, 0)

    def test_assign_group_splits_keeps_group_together(self):
        records = [
            type("R", (), {"row_number": idx, "outward_code": f"G{idx % 5}", "image_url": f"u{idx}", "type_value": "备选"})
            for idx in range(20)
        ]
        samples = assign_group_splits(build_labeled_samples(records))

        split_by_group = {}
        for sample in samples:
            split_by_group.setdefault(sample.outward_code, sample.split)
            self.assertEqual(split_by_group[sample.outward_code], sample.split)
            self.assertIn(sample.split, {"train", "val", "test"})

    def test_build_training_dataset_writes_jsonl_failures_and_standard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook = root / "labels.xlsx"
            output = root / "dataset"
            write_label_xlsx(workbook)
            image_data = jpg_bytes()
            legacy = output / "商品标注结果" / "train" / "OLD"
            legacy.mkdir(parents=True)
            (legacy / "old.jpg").write_bytes(image_data)
            legacy_original = output / "商品原始照片" / "OLD"
            legacy_original.mkdir(parents=True)
            (legacy_original / "old_original.jpg").write_bytes(image_data)
            legacy_result = output / "人工标注结果" / "OLD"
            legacy_result.mkdir(parents=True)
            (legacy_result / "old_result.jpg").write_bytes(image_data)

            def fetcher(url):
                if url.endswith("d.jpg"):
                    raise RuntimeError("offline")
                return image_data

            summary = build_training_dataset(workbook, output, download_workers=2, fetcher=fetcher)

            manifest = [json.loads(line) for line in (output / "manifest_all.jsonl").read_text().splitlines()]
            failed = [item for item in manifest if item["download_status"] == "failed"]
            positive = next(item for item in manifest if item["label"] == 1 and item["download_status"] != "failed")
            negative = next(item for item in manifest if item["label"] == 0 and item["download_status"] != "failed")
            product_data = output / "商品数据"
            original_files = list(product_data.glob("*/商品原始照片/*.jpg"))
            result_files = list(product_data.glob("*/人工标注结果/*.jpg"))
            positive_original_exists = Path(positive["original_image_path"]).exists()
            positive_result_exists = Path(positive["annotation_result_path"]).exists()
            positive_original_parts = Path(positive["original_image_path"]).relative_to(output).parts
            positive_result_parts = Path(positive["annotation_result_path"]).relative_to(output).parts
            positive_image_name = Path(positive["image_path"]).name
            split_dir_exists = any((product_data / positive["outward_code"] / "商品原始照片" / split).exists() for split in ("train", "val", "test"))
            legacy_result_exists = (output / "商品标注结果").exists()
            legacy_role_exists = any((output / role).exists() for role in ("商品原始照片", "人工标注结果"))
            archived_legacy = list((output / "_legacy").rglob("old*.jpg"))
            train_exists = (output / "train.jsonl").exists()
            standard_exists = (output / "product_labeling_standard.md").exists()
            standard_text = (output / "product_labeling_standard.md").read_text()

        self.assertEqual(summary["unique_items"], 4)
        self.assertEqual(summary["label_counts"]["positive"], 2)
        self.assertEqual(summary["label_counts"]["negative"], 2)
        self.assertEqual(summary["dataset_role_counts"]["商品原始照片"], 4)
        self.assertEqual(summary["dataset_role_counts"]["人工标注结果"], 2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["quality_metrics"], None)
        self.assertEqual(len(original_files), 3)
        self.assertEqual(len(result_files), 2)
        self.assertEqual(negative["dataset_roles"], ["商品原始照片"])
        self.assertIn("人工标注结果", positive["dataset_roles"])
        self.assertTrue(positive_original_exists)
        self.assertTrue(positive_result_exists)
        self.assertIn(positive_image_name, {"a.jpg", "b.jpg", "c.jpg"})
        self.assertEqual(positive_original_parts[:3], ("商品数据", positive["outward_code"], "商品原始照片"))
        self.assertEqual(positive_result_parts[:3], ("商品数据", positive["outward_code"], "人工标注结果"))
        self.assertFalse(split_dir_exists)
        self.assertFalse(legacy_result_exists)
        self.assertFalse(legacy_role_exists)
        self.assertEqual(len(archived_legacy), 3)
        self.assertIsNone(negative["annotation_result_path"])
        self.assertTrue(train_exists)
        self.assertTrue(standard_exists)
        self.assertIn("模型只学习通用质量标准", standard_text)
        self.assertIn("高风险降权", standard_text)
        self.assertIn("人工复核", standard_text)

    def test_cli_accepts_build_training_set_command(self):
        args = build_parser().parse_args(
            ["build-training-set", "--label-workbook", "测试集.xlsx", "--output-dir", "模型训练数据", "--download-workers", "3"]
        )

        self.assertEqual(args.command, "build-training-set")
        self.assertEqual(args.label_workbook, "测试集.xlsx")
        self.assertEqual(args.output_dir, "模型训练数据")
        self.assertEqual(args.download_workers, 3)


if __name__ == "__main__":
    unittest.main()
