from __future__ import annotations

from collections import Counter
from pathlib import Path


def write_full_evaluation_report(path: Path, summary: dict, products: list[dict], mismatches: list[dict]) -> None:
    lines = [
        "# 全量测试集人工匹配报告",
        "",
        "## 总体结果",
        f"- 商品分组数：{summary['products']}",
        f"- 图片样本数：{summary['samples']}",
        f"- 匹配数量：{summary['matched']}",
        f"- 不匹配数量：{summary['mismatches']}",
        f"- 人工匹配率：{summary['accuracy']:.6%}",
        f"- TP/FP/TN/FN：{summary['tp']}/{summary['fp']}/{summary['tn']}/{summary['fn']}",
        "",
        "## 不匹配商品汇总",
        "| outward_code | 总数 | 不匹配 | 匹配率 | FP | FN |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in sorted((item for item in products if item["mismatches"]), key=lambda item: (-item["mismatches"], item["outward_code"])):
        lines.append(f"| {row['outward_code']} | {row['total']} | {row['mismatches']} | {row['match_rate']:.4%} | {row['false_positive']} | {row['false_negative']} |")
    lines += ["", "## 不匹配可能原因汇总"]
    for reason, count in _reason_counts(mismatches).most_common():
        lines.append(f"- {reason}：{count}")
    lines += ["", "## 不匹配图片明细", "| outward_code | sample_id | 人工 | 模型 | 图片路径 | 可能原因 |", "|---|---|---:|---:|---|---|"]
    for row in sorted(mismatches, key=lambda item: (item["outward_code"], item["sample_id"])):
        lines.append(f"| {row['outward_code']} | {row['sample_id']} | {row['label']} | {row['prediction']} | {row['image_path']} | {row['reason']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reason_counts(mismatches: list[dict]) -> Counter:
    counts = Counter()
    for row in mismatches:
        for reason in row["reason"].split("；"):
            if reason:
                counts[reason] += 1
    return counts
