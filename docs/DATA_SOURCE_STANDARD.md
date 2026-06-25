# 数据源全局规范

## 目标

商品标注工作台的运行态、统计态、标注态数据统一以 SQLite 为准。默认数据库文件为 `goods_marking.db`。

## 数据源边界

### SQLite

SQLite 是唯一运行态数据源，页面接口只能从 SQLite 或由 SQLite 驱动的内存状态读取业务字段。

必须写入 SQLite 的数据：

- 商品进度。
- 商品图片明细。
- 图片是否为 `standard`。
- 第一张 `standard` 商品图片 URL。
- 人工标注状态。
- 商品统计需要的运行态字段。

### Excel

Excel 只作为导入和导出介质。

允许：

- 初始化导入商品 URL、source、原始行号。
- 最终交付时从 SQLite 导出人工标注状态。
- 人工审计和抽查。

禁止：

- 页面请求时扫描 Excel。
- 提交标注时写 Excel。
- 商品统计直接从 Excel 计算新增字段。
- 工作台在 SQLite 缺失时回退读取 Excel。

### CSV

CSV 只作为历史兼容和迁移输入。

允许：

- `workflow_progress.csv` 导入 SQLite。
- `人工标注状态.csv` 导入 SQLite。

禁止：

- 新增 CSV 作为运行态状态文件。
- 页面接口把 CSV 作为业务真值。

### 商品图片目录

`商品标注结果/` 保存图片文件和审计产物，不作为统计真值。

允许：

- 初始化或重建 SQLite 时读取图片文件路径。
- 图片服务按 SQLite/内存中的图片记录返回本地文件。

禁止：

- 每次商品统计请求实时扫描目录计算统计口径。

## 当前表职责

- `product_progress`：商品处理进度。
- `product_images`：商品图片明细，包含 `outward_code`、`image_url`、`source`、`row_number`、`is_standard`。
- `image_review_status`：人工标注状态，主键为 `outward_code + image_url`。

## 商品统计字段规则

- `商品图片`：读取 SQLite `product_images` 中当前商品第一张 `is_standard=1` 图片 URL。
- 第一张排序：`row_number` 最小优先，其次 `image_url` 字典序。
- 没有 `standard` 图片时返回空字符串，页面显示 `-`。
- 后续新增统计字段，必须先定义 SQLite 字段或 SQLite 查询规则，再接页面。

## 迁移命令

源 Excel、历史进度 CSV、历史人工状态 CSV 统一通过 `migrate-state` 导入 SQLite：

```bash
/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m image_workflow.cli \
  --state-db goods_marking.db \
  migrate-state \
  --source-workbook 2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度.xlsx \
  --progress workflow_progress.csv \
  --status-csv 2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度_人工标注状态.csv
```

## 本地调试

固定地址仍为 `http://127.0.0.1:8765/`。

默认启动命令：

```bash
/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m image_workflow.cli review-workbench \
  --state-db goods_marking.db \
  --result-dir 商品标注结果
```
