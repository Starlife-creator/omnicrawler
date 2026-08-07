# OmniCrawler 1.0.0 全量生产发行报告

## 发行定位

1.0.0 是重新编号后的第一个正式基线。它不是精简重写：原有采集、PDF、模板、
插件、质量、调度、存储、研究复现和开发者能力全部保留，并增加完整 Windows
自包含运行时。原始完整项目压缩包保持不变，便于审计和回退。

## 完整能力边界

- 本地独立能力：静态/动态采集、附件、PDF、Paddle/Tesseract OCR、SQLite、
  JSONL/CSV/Excel/Parquet/DuckDB、质量报告、复核、备份与研究包。
- 随包客户端能力：Redis、S3、PostgreSQL、OpenSearch。客户端依赖已包含；选择这些
  后端时仍需用户拥有对应外部服务和凭据。
- 合规边界：不绕过验证码、付费墙、访问控制、robots 或站点安全策略。

## 依赖闭环验证

- Python 3.13：全部 `full + dev` 依赖安装成功，`pip check` 无冲突，21 个关键模块
  逐项真实导入通过。
- 浏览器：Playwright Chromium 149 与 ChromeDriver 149；Playwright、Selenium
  动态页面与动作等待均通过真实本地 HTTP 测试。
- Tesseract：5.5.0.20241111，引擎与 `chi_sim + eng + osd` 模型枚举通过。
- PaddleOCR：PaddlePaddle 3.3.1、PaddleOCR 3.7.0、PPStructureV3 的 11 个模型共
  1,088,180,035 字节；Windows CPU 推理通过。因上游 oneDNN/PIR 兼容问题，Windows
  默认使用功能完整的常规 CPU runner。

## 用户体验

- 第一页直接输入网址，自动补全协议，主“下一步”按钮在高 DPI/低高度窗口可见。
- 五步向导长页可滚动，首次启动直接聚焦网址，不再用模态提示阻塞。
- GUI 自动找到同目录 CLI 与本地文档，设置/结果保存在便携目录。
- 简单/专业/开发者三种模式不丢配置；新增完整运行能力面板。

## 工程与发布结构

- `src/omnicrawl` 保持模块化；插件 API v1 与配置迁移协议保留。
- Windows、Linux、macOS、Docker 与直接 `python -m`/CLI 入口分别维护。
- Windows 便携包只包含 Windows 二进制和用户/运维文档，不携带其他系统运行时。
- 源码包排除 `.venv`、构建物、缓存和历史发布 ZIP；全部平台源码与脚本保留。

最终源码门禁为 88 passed、2 条件跳过，Ruff/compileall 通过，Mypy 68 个源文件
零问题；67 套含 legacy 模板全部有效。Windows 便携产物的 Playwright、Selenium、
Tesseract 与 PaddleOCR 真实离线冒烟全部通过。精确哈希见交付目录
`SHA256SUMS-1.0.0.txt`，详细过程见 `docs/TEST_REPORT.md`。
