# 本地调试规范

## 固定地址

- 商品标注工作台本地调试统一使用 `http://127.0.0.1:8765/`。
- 默认 host/port 由 `image_workflow.cli` 的 `DEFAULT_REVIEW_HOST` 和 `DEFAULT_REVIEW_PORT` 管理。
- 不使用临时端口作为常规调试入口；只有确认 `8765` 被非本项目进程占用且无法停止时，才临时换端口并明确说明。

## 启动命令

```bash
/Users/henry/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m image_workflow.cli review-workbench \
  --status-csv 2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0_带处理进度_人工标注状态.csv \
  --result-dir 商品标注结果
```

## 端口冲突处理

- 启动前先检查 `8765` 是否已有 `review-workbench` 进程在监听。
- 如果是旧的调试进程，先停止旧进程，再启动新进程。
- 默认不扫描大型 Excel；需要全量商品统计时才追加 `--source-workbook 2026-06-23-20-22-38_EXPORT_XLSX_26258034_453_0.xlsx`。
- 页面首次打开时允许显示“数据加载中”，等待后台加载完成。
