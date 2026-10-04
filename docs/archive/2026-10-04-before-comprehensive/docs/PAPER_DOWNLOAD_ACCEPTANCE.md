# 公开论文附件下载与市场插件审查

> 适用版本：0.15.0 · 配置协议：v5 · 维护状态：现行

本轮选择宿主已有 Pipeline 公开 PDF 附件流程作为可验证交付路径。市场 academic-paper-downloader 固定快照仍保留，但未获得宿主场景验收。

## 可执行的宿主流程

从明确允许的论文列表或所选 PDF URL 建立普通配置，用 plan 预览范围；seeds 仅保留所选资源，范围与 CDN 域名必须显式审阅。复用现有下载、数据库、取消恢复与导出，不新增下载器或 ffmpeg。

```yaml
project: {name: papers, workspace: work/papers}
source: {kind: static_html, seeds: [https://example.org/papers]}
crawl: {max_pages: 10, max_depth: 1, concurrency: 1, same_host: true}
http: {respect_robots: true, delay_seconds: 1.5}
egress: {allowed_domains: [example.org], maximum_requests: 30}
download: {enabled: true, extensions: [.pdf], verified_pdf_manifest: true}
extract: {mode: auto, fields: {}, review_low_confidence: true}
outputs: {jsonl: true, csv: true, xlsx: false}
```

```bash
omnicrawler validate -c papers.yaml
omnicrawler plan -c papers.yaml
omnicrawler run -c papers.yaml
omnicrawler recovery failures -c papers.yaml --limit 100
omnicrawler recovery retry-failed -c papers.yaml --fingerprint REQUEST_SHA256
omnicrawler resume -c papers.yaml
omnicrawler export -c papers.yaml
```

failures 返回请求指纹、去除查询凭据的 URL、尝试次数和原因码，不输出原始异常中的凭据；列表截断会明确标记。选定重试支持重复 --fingerprint，去重且只复位这些 failed 请求；done/blocked/无关失败保留。认证过期不能通过此入口跳过登录代次验证，须使用已验证登录恢复。工作区仍有任务资源时拒绝修改。未传 fingerprint 的历史全失败重试行为保持兼容。

verified_pdf_manifest 在任务实际导出阶段启用，与 PDF 解析/OCR 分开。source_manifest.jsonl 保存累计 PDF 的来源 URL、父页、请求指纹、运行 ID、文件大小、哈希与 verified_file 状态；磁盘文件必须与完成记录一致，越界/链接/损坏时拒绝生成新清单。缺失文件明确计入 missing_files，不算完整交付。校验累计 PDF 有磁盘读取成本，通用任务默认关闭；学术模板显式开启。

中断后从数据库恢复，重新下载所选失败资源；没有 Range/对象版本证据时不承诺字节续传。测试使用本地受限 HTTP 站点、robots、故障与恢复，验证累计成果、重复执行和原文件完整性；不代表公网上所有出版社、授权订阅或真实 PDF 内容解析已验收。

## 市场插件路线停止证据

快照 `87443026e414341b6a2a6b302ac927f33dbf201d` 的插件 YAML 为 0.6.4，包清单标记 0.3.0，plugin.py SHA-256 为 `5e8628b3a5837f83f10996c6960c21428edb760789f626af8dab08e7dd00b23d`，与该清单声明不一致。只读审查还发现 httpx 直接网络和自建 Playwright 上下文；Cookie 通过插件 state 命名空间保存，不能视为已映射到宿主会话中心。

```bash
python tools/audit_paper_scenario.py ../OmniCrawler-market/plugins/academic-paper-downloader --json audit.json
```

实际宿主加载在执行代码前以“插件不得直接导入网络客户端”拒绝。此审查未执行插件、未重新验签，不以维护者签名代替运行边界。保留市场受签名字节与固定快照，不添加 AST 豁免、不直连网络、不读取私钥。

依专项方案退出条件停止融合该版本。再次进入须迁移到 SDK 网络/受控文件/作用域会话、发布清单一致的新签名版本，再验证整场景、失败分类和累计成果；当前没有已授权私钥路径或此新产物，不能声明插件完整交付通过。
