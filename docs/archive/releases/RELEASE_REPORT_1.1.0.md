# OmniCrawler 1.1.0 发行与验收报告

发行日期：2026-07-21

## 范围

本版本落实 1.0.0 项目结构审计及用户反馈：傻瓜式任务向导、动态站点/后台接口、正确 URL 种子指导、
栏目主题 PDF、同址变化、模板解释与组合、字段级问号帮助、可选 AI Provider，以及相关配置一致性和
保存/执行缺陷修复。

## 兼容性

- Python 3.10+；插件 API 仍为 v1。
- 配置协议为 v5；加载时非破坏迁移 v1-v4，未知字段继续保留。
- `source.kind: rss` 迁移为 `feed`；GUI 和内核统一使用 `feed`。
- `crawl.pagination` 迁移至 `source.pagination`，`param` 迁移为 `parameter`。
- JSON 提取的 `item_selector` 迁移为 `item_path`。
- 旧工作区的 SQLite `responses` 表在打开时自动增加 ETag/Last-Modified 列。

## 已知边界

- 操作学习不绕过验证码、付费墙或访问控制；一次性 token/签名需合法会话或凭据提供器。
- API 自动发现先输出证据与样本验证状态；复杂加密请求、WebSocket 或非确定性签名可能继续使用浏览器。
- `ocr_backend: none` 配合 `skip_ocr: false` 表示自动策略；离线 OCR 需要对应 Standard/Full 组件。
- 本交付是完整 1.1.0 源码包；Windows 可执行便携版需在目标构建环境运行 `build_windows.ps1`。

## 验收

- Python 源码编译检查。
- 全量 pytest：`94 passed, 2 skipped`，包括新增 TaskSpec、主题筛选、POST API 发现、帮助覆盖、
  AI Provider、条件请求与 GUI。
- 模板健康检查，确认占位符声明、动作名和配置结构。
- 源码 ZIP 使用可复现 ZIP64 工具生成，并提供独立 SHA-256 清单。
