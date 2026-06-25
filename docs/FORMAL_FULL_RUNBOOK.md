# 正式商品标注全量执行工作流

本文档用于在同一个项目 `/Users/henry/Documents/商品标注` 下继续正式商品标注任务。新会话进入该目录后，先读本文档，再执行或补齐正式流程。

## 1. 当前目标

- 使用测试集训练出的通用质量模型和现有算法，替代早期纯规则筛选。
- 覆盖源 Excel 中全部 `outward_code` 商品分组。
- 每个商品先下载完整原始图片，再进行模型评分、规则过滤、去重和最终筛选。
- 正式交付目录固定为 `商品标注结果/{outward_code}/最终结果/`。
- 不强行凑满 40 张；不足时保留可用结果并标记 `shortfall`。

## 2. 关键文件和目录

| 用途 | 路径 |
| --- | --- |
| 源 Excel | `2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0.xlsx` |
| 人工状态 CSV | `2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度_人工标注状态.csv` |
| 模型文件 | `模型训练数据/model/annotation_model.json` |
| 正式结果根目录 | `商品标注结果/` |
| 全局进度表 | `workflow_progress.csv` |
| 当前 100 商品汇总 | `商品标注结果/formal_100_summary_20260624_222420.csv` |
| 阈值说明 | `阈值配置手册.md` |
| 工作台服务 | `image_workflow/review_server.py` |
| 正式算法入口 | `image_workflow/formal_workflow.py` |

## 3. 正式结果目录结构

每个商品目录应保持如下结构：

```text
商品标注结果/
  {outward_code}/
    商品原始照片/
    模型选中/
    模型排除/
    需人工复核/
    最终结果/
    manifest.csv
    model_scores.csv
    selection_report.json
    qa_summary.txt
    contact_sheet.jpg
    verification_report.json
```

说明：

- `商品原始照片/`：该商品下载后的全部原始候选图，筛选前必须完整。
- `模型选中/`：模型和硬规则认为可进入最终结果的候选追踪目录。
- `模型排除/`：模型或规则排除的图片。
- `需人工复核/`：`standard` 参考图或弱判断图片，不直接进入正式结果。
- `最终结果/`：正式交付目录，工作台和人工复核以这里为主。
- 重新跑同一商品时，已有目录会先移动到 `商品标注结果/bak/{outward_code}_{timestamp}/`。

### 图片命名规范

- 所有图片目录（`商品原始照片/`、`模型选中/`、`模型排除/`、`需人工复核/`、`最终结果/`、训练缓存）统一使用图片 URL path 的原始文件名。
- URL 中的转义路径会先解码，例如 `tagmark%2Fframe_188_1782217054_0478b5.jpg` 保存为 `frame_188_1782217054_0478b5.jpg`。
- 同一目录内如果 URL 原始文件名冲突，后续文件追加 `_毫秒时间戳`，例如 `same_1782370000000.jpg`，禁止覆盖已有图片。
- 文件名不再承载 `row_number`、`source`、模型分、视角、排序等业务信息；这些信息必须写入 `manifest.csv`、`model_scores.csv`、`selection_report.json` 或训练 JSONL 字段。
- `最终结果/` 的文件名必须和对应 URL 原图文件名一致，不再添加 `01_front_label__001__` 这类角度/排序前缀。
- 新增或修改命名逻辑后必须运行 `python3 -m image_workflow.rename_migration --root .`，结果应为 `planned_renames: 0`。

## 4. 当前 100 个商品状态

截至本文档生成时：

- `workflow_progress.csv` 共 `39833` 个商品分组。
- 已跑前 `100` 个商品。
- 结果状态：`complete=43`，`shortfall=57`，`pending=39733`。
- `formal_100_summary_20260624_222420.csv` 中 `verified=True` 为 `67`，`verified=False` 为 `33`。
- 当前 100 个商品共选出 `2469` 张最终图片。

这些数字是续跑前的基线；新会话应先重新读取当前文件确认是否有变化。

## 5. 正式单商品处理流程

正式流程入口是：

```python
from image_workflow.formal_workflow import process_formal_group

report = process_formal_group(
    records,
    "商品标注结果",
    "模型训练数据/model/annotation_model.json",
    target_count=40,
    download_workers=4,
)
```

处理顺序：

1. 根据 `outward_code` 准备 `商品标注结果/{outward_code}/`，如有旧结果先备份到 `bak/`。
2. 如果该商品全部下载链接都包含 `standard`，直接跳过，不下载，不进入最终结果，并记录：
   - `status=skipped_all_standard`
   - `skip_reason=all_image_urls_contain_standard`
   - `最终结果是否包含该图片=否`
3. 下载该商品全部原始图片到 `商品原始照片/`，文件名按“图片命名规范”与 URL 保持一致。
4. 下载不完整时标记 `download_incomplete`，该商品不进入筛选。
5. 加载 `annotation_model.json`，对每张图计算质量指标和模型分。
6. 以 `manifest.csv` 中 `source=standard` 的参考图建立目标商品颜色和形态参考，但 `standard` 本身不进入最终结果；禁止再依赖文件名包含 `standard` 判断参考图。
7. 应用硬排除规则，包含白底、文件过小、过暗、多商品、边界不清、不完整、重复部件、目标占比过低、疑似手机等。
8. 对剩余候选按模型分排序，使用感知哈希去重，最多复制 `40` 张到 `最终结果/`，最终文件名保持 URL 原始文件名。
9. 生成 `manifest.csv`、`model_scores.csv`、`selection_report.json`、`qa_summary.txt`、`contact_sheet.jpg`、`verification_report.json`。
10. 选中不足 40 张时保留实际数量并记录 `shortfall`，不得用明显不合格图片凑数。

## 6. 不要直接使用旧 `run-full`

当前 `python3 -m image_workflow.cli run-full` 仍调用旧的 `selection.py` 规则流，不是这 100 个商品使用的正式模型流程。

全量执行前应先补齐或使用一个 formal runner，核心要求：

- 读取 `.image_workflow_cache/group_index.csv` 中的商品分组文件；没有索引时用 `build_group_index()` 生成。
- 跳过 `workflow_progress.csv` 中已完成的 `complete`、`shortfall`、`skipped_all_standard`，除非明确要求重跑。
- 每个商品调用 `process_formal_group()`，不要调用旧 `process_group()`。
- 默认并发：`group_workers=3`，每组 `download_workers=4`。
- 进度 CSV 必须单写入者或加锁写入；历史上并发写 `workflow_progress.csv` 出现过 NUL 字符损坏。
- 每处理完一个商品立即写入进度和汇总，支持中断续跑。

推荐 runner 逻辑：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from image_workflow.group_index import build_group_index, iter_group_files, read_group_records
from image_workflow.formal_workflow import process_formal_group

workbook = "2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0.xlsx"
index_dir = Path(".image_workflow_cache")
result_root = Path("商品标注结果")
model_path = Path("模型训练数据/model/annotation_model.json")
progress_path = Path("workflow_progress.csv")

if not (index_dir / "group_index.csv").exists():
    build_group_index(workbook, index_dir, progress_path)

pending_files = select_pending_group_files(iter_group_files(index_dir), progress_path)

def run_one(group_file):
    records = read_group_records(group_file)
    report = process_formal_group(records, result_root, model_path, target_count=40, download_workers=4)
    update_progress_and_summary_under_lock(progress_path, report)
    return report

with ThreadPoolExecutor(max_workers=3) as executor:
    for future in as_completed([executor.submit(run_one, f) for f in pending_files]):
        print(future.result())
```

## 7. 工作台流程

启动工作台：

```bash
/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m image_workflow.cli review-workbench \
  --status-csv /Users/henry/Documents/商品标注/2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度_人工标注状态.csv \
  --result-dir /Users/henry/Documents/商品标注/商品标注结果 \
  --host 127.0.0.1 \
  --port 8765
```

如果需要用源 Excel 补充全量商品统计，可额外传入：

```bash
--source-workbook /Users/henry/Documents/商品标注/2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0.xlsx
```

工作台规则：

- 默认不扫描 XLSX，顶部指标从结果目录和人工状态 CSV 汇总；显式传入 `--source-workbook` 时才读取源 Excel 做全量商品统计。
- 商品默认展示模型最终结果。
- 模型最终结果页默认状态为合格；勾选后表示不合格；提交本商品后，该商品标记完成。
- 商品原始照片页展示全部原图状态：`未标注`、`合格`、`不合格`、`合格待确认`。
- 原图若已在 `最终结果/` 且没有人工标注状态，显示 `合格待确认`，样式需与其他图片区分。
- 原图点击切换规则：`未标注/不合格 -> 合格`，`合格/合格待确认 -> 不合格`。
- 人工状态只写入人工状态 CSV 的 `人工标注状态` 字段，不再写状态 XLSX。
- 服务默认只扫描结果目录和 CSV；只有显式传入 `--source-workbook` 时才会扫描较大的源 Excel。

## 8. 全量执行前检查清单

1. 当前目录是 `/Users/henry/Documents/商品标注`。
2. 模型文件存在：`模型训练数据/model/annotation_model.json`。
3. 源 Excel 存在；人工状态 CSV 存在或可由工作台首次提交时创建。
4. `workflow_progress.csv` 能正常读取，没有 NUL 字符。
5. `商品标注结果/bak/` 有足够磁盘空间容纳重跑备份。
6. 确认 runner 调用的是 `process_formal_group()`。
7. 先跑 1 到 3 个 `pending` 商品检查目录结构和报告，再扩大到全量。
8. 全量期间不要同时启动多个会写 `workflow_progress.csv` 的任务。

## 9. 新会话启动提示

新会话应按下面顺序继续：

1. 打开同一个项目目录 `/Users/henry/Documents/商品标注`。
2. 阅读本文档、`image_workflow/formal_workflow.py`、`workflow_progress.csv`、`商品标注结果/formal_100_summary_20260624_222420.csv`。
3. 确认当前 100 商品是否仍是最新基线。
4. 如需全量执行，先补齐正式 formal runner 或 CLI 命令，再从 `workflow_progress.csv` 的 `pending` 商品续跑。
5. 跑完一批后用工作台人工复核，并将人工状态写回人工状态 CSV。

## 10. 验证命令

文档或 runner 修改后至少执行：

- `/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests`
- `/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall image_workflow tests`

只查看当前 100 商品汇总时，读取 `workflow_progress.csv` 和 `商品标注结果/formal_100_summary_20260624_222420.csv` 统计 `status`、`verified` 即可。
