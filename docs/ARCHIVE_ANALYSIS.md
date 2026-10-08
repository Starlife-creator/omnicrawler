# 选定交付文档分析

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

`analyze-archive` 只分析清单明确选中的本地文档，不扫描整个工作区。复用 document_ir，不引入 LangGraph、转录服务或新常驻进程。文档必须在清单目录内、非链接，SHA-256 与交付时保存值一致；支持格式以 document_ir 实际注册为准。

```json
{"format":1,"sources":[{"id":"report-2026-10","path":"report.txt","sha256":"填写交付文件实际的SHA-256","source_url":"https://example.org/report"}]}
```

```bash
omnicrawler analyze-archive --manifest delivered/manifest.json -o analysis
omnicrawler analyze-archive --manifest delivered/manifest.json -o analysis --ai --ai-config task.yaml
```

默认只生成 facts.json 与 analysis.json，包含来源哈希、段落定位、稳定证据 ID 和统计；解释与建议为空。文字型 PDF 保存真实页码与页内段落；能与原生单词逐段核对时保存区域 bbox，否则只保留页/段定位，不推测坐标。表格保留单元格、页内表序号及可用区域，事实证据保留表格行和原文引用。段落证据至多 400 条、100000 字符，每段至多 2000 字符，报告明确说明遗漏段落未分析；不能据此声称覆盖全文或因果成立。

所选原文件合计至多 40 MiB；本地摘录预算按文档分配。模型轮流取各来源的摘录，model_scope 记录实际覆盖、遗漏来源及证据 ID，不将未发送的文档算作模型已分析。

仅 `--ai` 请求模型，并受任务 AI 开关、allow_page_text、EgressBroker 与更小的字符、单次输出和费用预算约束，每次执行至多一次模型请求。发送所选证据摘录，不发送其他文档。模型解释必须附不确定性及逐字原文引用；未知证据 ID、编造引文或额外字段拒绝。所有报告 requires_review=true，模型不会自动执行建议。

事实阶段先原子保存。模型、预算或输出校验失败生成 status=paused 的报告，再次执行复用哈希仍匹配且阶段摘要完整的事实。损坏阶段重新解析；原始交付改变则先拒绝，需更新清单明确选择新交付。不保存模型错误中的原文/秘密。输出可能包含所选原文，请按交付文档相同权限管理。

本地验证覆盖实际 CLI、可回链事实、缓存完整性、变更拒绝、摘录限制、失败恢复和受控模型引文；外部模型与真实费用未测。2026-10-04 补验覆盖本机原生 PDF 与真实 Tesseract OCR；完整主程序冻结环境仍未测，独立 OCR 组件试点见本轮报告。

文字型 PDF（2026-10-04）：document_ir 已接入现有 pdfx 原生解析，证据 locator 保存真实页码与页内段落，报告保存解析警告和遗漏页。默认最多 200 页，空白/扫描/严重乱码页不启动 OCR，明确标为遗漏；完全没有可用原生文字则拒绝，不生成虚假的成功报告。新事实阶段版本为 4，旧缓存须重建；缓存完整性校验涵盖段落、表格和定位证据。分析默认本地，AI 仍需显式启用。

2026-10-08 人工确认续表：PDF 清单条目可提供 `parse_options`，仅接受 `confirmed_table_continuations`、`column_boundaries`、`auto_columns`、`max_pages`。例如 `"parse_options":{"confirmed_table_continuations":[[0,1],[1,2]]}` 使用从 0 开始的原始表序号，表示用户已明确确认这些候选属于同一续表；只允许解析器实际标记的候选，重复、逆序、越界或非法序号拒绝。不会仅凭相同表头自动合并。也可用相同选项调用 `parse_document`；OCR 后端/凭据不接受为清单选项。

确认后的逻辑表仍保留原始表定位，每一行另有 `row_locators`。分析证据中的 `table`/`row` 是合并视图序号，`source_table`/`source_row`、`page`、`bbox`、`node_id` 对应原始来源；不能把第二页数据行误标为第一页。重复表头从视图中移除，原始文档和来源表引用完整保留；GUI 校正导出的 document.json 同样包含逐行映射。确认的是续表关系，不证明数值、单位、主体和期间都正确。

当前事实阶段版本为 8，布局/确认选项进入缓存身份，改变选项会重建事实；旧缺少逐行来源的事实缓存也重建。文档哈希、摘录和报告限制继续有效，不能用合并后的局部摘录宣称全文完整。
