# docs/archive — 归档区（内容已冻结）

> **本目录下所有内容描述的是历史状态，不代表当前行为。**
> 查阅当前行为请以 [文档导航](../README.md) 第 2 节的现行文档为准。

## 为什么保留

这些材料记录了当时的决策理由、审计过程与评估结论。删除会丢失可追溯性，因此保留在仓库内，
但从现行文档索引中移除，避免被误当作操作依据。

## ⚠️ 阅读前必读：版本号经历过重置

`docs/archive/releases/` 下的 `RELEASE_REPORT_1.x` / `2.x` 属于**旧编号体系**。

当前版本线为 **0.12.0**（见 `pyproject.toml` 与 `src/omnicrawler/__init__.py`）。
归档中的 `RELEASE_REPORT_2.3.1.md` 等文件版本号虽然"更大"，但**时间上更早**，
把它与当前 0.12.0 做任何大小比较都是错的。当前版本的发布报告位于
[`docs/releases/`](../releases/README.md)。

## 内容构成

| 路径 | 内容 |
|---|---|
| `compatibility/` | 旧版本（1.1.2 起）的兼容性与迁移说明，共 17 篇 |
| `releases/` | 旧编号体系下的发布报告（1.0.0 – 2.6.0），共 18 篇 |
| `audit-20260805/` | 2026-08-05 全量代码审计报告，含各子模块报告与自身 README |
| `final-release-audit-20260725/` | 2026-07-25 发布前审计，含 SHA256SUMS |
| `omnicrawler-assessment/` | 项目评估 HTML 报告 |
| `omnicrawler-evaluation-report/` | 项目评估 HTML 报告（含第三方静态资产，见下） |
| `OPTIMIZATION_PLAN_*.md`、`optimization-plan-v3.md` | 历史优化方案与跟踪记录 |
| `OmniCrawler-0.5.0-*.md` | 早期 Agent 上下文与提示词存档 |
| `omnicrawler-final-summary.html` | 历史总结报告 |

## ⚠️ 禁止清理的部分

`omnicrawler-evaluation-report/_shared/` 下存放**随仓库再分发的第三方资产**：

- `_shared/js/mermaid.min.js`（MIT）
- `_shared/js/echarts.min.js`（Apache-2.0）
- `_shared/fonts/*.ttf`（Instrument Sans / JetBrains Mono / Outfit，SIL OFL 1.1）

根目录 [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) 通过引用这些路径完成许可声明。
**删除该目录会让许可声明悬空，造成合规缺口。** 任何清理动作都必须先处理许可声明。

## 维护约定

- 新文档默认不进归档区；确需归档时在文件首部标注冻结版本与归档日期。
- 本门页由 `tools/check_docs_consistency.py` 校验存在性，请勿删除。
