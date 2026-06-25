# 全量测试集验收流程

## 适用场景
- 人工测试集或校准模型更新后，复跑全量一致性验收。
- 检查模型输出与人工标注的整体匹配率、商品级不匹配分布、图片级不匹配原因。
- 当前口径用于测试集校准回放，不代表未知商品泛化准确率。

## 前置文件
- `模型训练数据/manifest_all.jsonl`
- `模型训练数据/model/calibrated_hash_model.json`

若缺少校准模型，先运行：

```bash
python3 -m image_workflow.calibrated_hash_model --dataset-dir 模型训练数据 --output-dir 模型训练数据/model
```

## 固定验收命令

```bash
python3 -m image_workflow.cli evaluate-full-testset --dataset-dir 模型训练数据 --model-dir 模型训练数据/model
```

如果只需要 CSV/Markdown，不需要生成图片预览：

```bash
python3 -m image_workflow.cli evaluate-full-testset --dataset-dir 模型训练数据 --model-dir 模型训练数据/model --no-preview
```

## 输出文件
- `模型训练数据/model/full_testset_match_report.md`：整体匹配率、商品级不匹配汇总、图片级不匹配明细。
- `模型训练数据/model/full_testset_mismatches.csv`：不匹配图片明细，包含 `outward_code`、`sample_id`、人工标签、模型预测、图片路径、URL、可能原因。
- `模型训练数据/model/full_testset_product_summary.csv`：每个商品的总数、匹配数、不匹配数、匹配率、FP/FN。
- `模型训练数据/model/full_testset_predictions.jsonl`：全量逐图预测结果。
- `模型训练数据/model/full_testset_mismatch_preview.jpg`：不匹配图片预览图。

## 验收口径
- `label=1`：人工选中。
- `label=0`：人工备选未选中。
- `prediction=1`：模型判定选中。
- `prediction=0`：模型判定备选未选中。
- 匹配率计算：`matched / total`。
- FP：人工备选、模型选中。
- FN：人工选中、模型备选。

## 当前基线
- 商品分组数：`110`
- 图片样本数：`20448`
- 匹配数量：`20429`
- 不匹配数量：`19`
- 人工匹配率：`99.907081%`
- TP/FP/TN/FN：`4160/17/16269/2`

## 不匹配原因解读
- `同一感知哈希簇存在人工正负标签冲突`：高度相似图片在人工标注中同时出现正负标签，模型按哈希簇多数标签回放。
- `人工为备选，但同哈希簇多数为选中`：可能是人工漏选、重复帧口径不一致，或与选中图高度近似。
- `人工为选中，但同哈希簇多数为备选`：可能是边界合格样本、人工口径不一致，或聚合规则导致正样本被少数化。
- `图片质量标志`：命中小文件、边缘裁切、白底、主体不完整、多个商品等风险项。

## 注意事项
- 每次更新 `测试集.xlsx` 后，应先重新构建训练数据和校准模型，再跑本验收命令。
- 测试集校准模型会使用同一份人工标注数据生成哈希标签表，只能用于测试集一致性核对。
- 生产筛选未知商品时，仍应结合通用质量模型、规则筛选、建模图参考和人工复核。
