# 选定交付文档分析

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

`analyze-archive` 只分析清单明确选中的本地文档，不扫描整个工作区。复用 document_ir，不引入 LangGraph、转录服务或新常驻进程。文档必须在清单目录内、非链接，SHA-256 与交付时保存值一致；支持格式以 document_ir 实际注册为准。

```json
{"format":1,"sources":[{"id":"report-2026-10","path":"report.txt","sha256":"填写交付文件实际的SHA-256","source_url":"https://example.org/report"}]}
```

```bash
omnicrawler analyze-archive --manifest delivered/manifest.json -o analysis
omnicrawler analyze-archive --manifest delivered/manifest.json -o analysis --ai -c task.yaml
```

默认只生成 facts.json 与 analysis.json，包含来源哈希、段落定位、稳定证据 ID 和统计；解释与建议为空。PDF 页码未知，不编造页级定位。段落证据至多 400 条、100000 字符，每段至多 2000 字符，报告明确说明遗漏段落未分析；不能据此声称覆盖全文或因果成立。

所选原文件合计至多 40 MiB；本地摘录预算按文档分配。模型轮流取各来源的摘录，model_scope 记录实际覆盖、遗漏来源及证据 ID，不将未发送的文档算作模型已分析。

仅 `--ai` 请求模型，并受任务 AI 开关、allow_page_text、EgressBroker 与更小的字符、单次输出和费用预算约束，每次执行至多一次模型请求。发送所选证据摘录，不发送其他文档。模型解释必须附不确定性及逐字原文引用；未知证据 ID、编造引文或额外字段拒绝。所有报告 requires_review=true，模型不会自动执行建议。

事实阶段先原子保存。模型、预算或输出校验失败生成 status=paused 的报告，再次执行复用哈希仍匹配且阶段摘要完整的事实。损坏阶段重新解析；原始交付改变则先拒绝，需更新清单明确选择新交付。不保存模型错误中的原文/秘密。输出可能包含所选原文，请按交付文档相同权限管理。

本地验证覆盖实际 CLI、可回链事实、缓存完整性、变更拒绝、摘录限制、失败恢复和受控模型引文；外部模型、真实费用与 OCR/PDF 冻结环境不属于本批验收。
