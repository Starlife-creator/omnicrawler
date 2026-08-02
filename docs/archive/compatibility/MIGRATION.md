# 基线项目融合与迁移

## `web_crawler_types_examples.zip`

19 类采集模式已统一为来源、抓取器、处理器和 YAML 示例。URL 规范化、robots、限速、请求恢复和文件命名不再由各示例重复实现。

## `公告采集分析工具_v4.0_模块化项目.zip`

`SiteAdapter`、会话重试、SQLite 断点、附件完整性和 Excel 安全已泛化到采集平台。特定公告站点应迁移为 `examples/plugins/` 中的来源插件，不要把站点选择器写进 Pipeline。

## `PDF批量数据抽取系统_完整项目.zip`

原生文字、OCR、候选页、规则/LLM、归一化、校验、复核和五表导出保留在 `src/omnicrawl/pdfx/`。PDF 模板的 `fields` 结构保持兼容。

## `PDF批量数据工作台_v5.0_融合模块化项目.zip`

v2 进一步融合了：

- `service.py` 处理/抽取稳定 API 和阶段回调；
- `project.py` PDF 子项目创建与模板校验；
- `text_export.py` 的 TXT、逐页 JSONL 和文本清单；
- `source_manifest.jsonl` 来源协议和 `source_meta_json`；
- `safe_regex.py` 的正则长度、输入长度、嵌套量词和可选超时保护；
- 损坏/加密 PDF 状态保留、下游重置和数据库自动迁移；
- 独立 `pdf-process`、`pdf-extract` 和统一工作台。

原 v5 内置公告爬虫没有再平行保留一套运行实现，因为 OmniCrawler 已覆盖更多来源类型。两者改用来源清单解耦。

## 从 OmniCrawler v1 升级

1. 保留 v1 工作目录作为备份。
2. 将自定义 YAML 复制到 v2 的 `configs/`，先运行 `omnicrawl validate`。
3. 站点插件复制到 `examples/plugins/`，通过 `omnicrawl plugins -c ...` 确认注册。
4. 不要直接复制 v1 `pdf/work/pipeline.sqlite3`；将 PDF 输入保留，由 v2 新项目扫描建库最稳妥。
5. 如果必须延续旧 PDF 库，先备份，然后用 `pdf-process ... status` 触发新增列迁移并进行小样本复查。

## 配置映射

| 旧项 | v2 位置 |
|---|---|
| 公告站点请求参数 | `source` 或来源插件 |
| 关键词聚焦 | `crawl.focus_keywords` |
| 附件下载目录 | `<workspace>/artifacts/` |
| PDF 字段 YAML | `processors.pdf.config` 模板 |
| PDF 运行配置 | 自动生成的 `<workspace>/pdf/project.yaml` |
| 爬虫下载元数据 | `<workspace>/artifacts/pdf/source_manifest.jsonl` |
| PDF 中间文本 | `<workspace>/output/pdf/text/` 和 `pages.jsonl` |
| 人工复核 | `review_queue.csv` → `pdf-extract apply-review` |

所有迁移都应在新工作目录做小样本试跑，不要覆盖原压缩包和旧数据库。
