# OmniCrawler 2.3.1 发布报告

**发布日期**: 2026-07-27
**基线版本**: 2.3.0
**发布类型**: 功能增强与视觉完善

## 概述

2.3.1 在 2.3.0 优化基础上，新增 GUI 视觉系统、无障碍能力、国际化管道和工程工具链改进。公共 API 语义不变。

## 关键变更

### GUI 视觉与无障碍
- 新增 3 套完整主题：明亮 / 暗黑 / 高对比度 + 色盲友好配色。
- 语义化色彩令牌（VisualTokens）：全局 QSS 覆盖 40+ 控件。
- 全局焦点可视化：所有可交互控件 2px 焦点框。
- 5 个 Wizard 步骤页 ARIA 标签补齐，屏幕阅读器支持。
- 减帧模式（reduced-motion）信号总线支持。
- 16 个 SVG Feather 风格矢量图标管线。
- QSS 缓存机制提升 GUI 渲染性能。

### 国际化（i18n）
- 556 条界面字符串已提取为 `.pot` 模板。
- 英文翻译 `.po` 就绪（部分覆盖，约 13%）。
- 新增 `tools/extract_i18n.py`、`tools/generate_en_po.py`、`tools/compile_i18n.py` 工具链。

### CLI 重构
- CLI 从 if/elif 链重构为字典注册表模式。
- 新增 `cli_commands.py` 和 `cli/_registry.py` 模块化命令注册。

### 性能与质量
- 新增性能基准框架：BenchmarkProfile/BenchmarkRunner/BenchmarkHistory + 20 个测试。
- 覆盖率阶梯门禁提升至 72%（目标 80%，核心 ≥85%）。
- mypy strict 渐进覆盖：GUI Phase 1 已纳入，逐步收紧。
- 349 passed / 22 skipped。

## 验证

- 349 passed, 22 skipped
- 覆盖率 ≥72% 门禁
- mypy (gui/core strict) 通过
- ruff 0 violations
- pre-commit hooks 已配置

## 兼容性

- 所有 2.3.0 配置文件无需修改即可使用
- 公共 API 语义不变
- CLI 子命令语义保持稳定
- GUI 三种模式（简单/专业/开发者）保持稳定
