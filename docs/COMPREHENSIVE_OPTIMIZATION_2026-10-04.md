# 2026-10-04 全面优化实施与验收 —— 归档入口

> 适用版本：1.0.18 · 配置协议：v5 · **维护状态：已完成并归档（2026-11-19）**

完整原文已按原始字节归档至
[历史记录](archive/2026-10-04-before-comprehensive/docs/COMPREHENSIVE_OPTIMIZATION_2026-10-04.md)。
历史结论仅对应原日期、提交 `f3278e3` 与环境，**不因归档升级为当前验收通过**。

## 为什么归档

该轮优化的**已落地行为、验收结论与测量数据**都已闭环；继续以正文形式留在
`docs/` 下，会让读者把它当成「当前状态」。按本仓惯例（见
[`archive/README.md`](archive/README.md)）改为归档入口。

**未完成项不会因归档而消失**，仍登记在下列现行文档：

| 想看什么 | 看哪里 |
|---|---|
| 当前能力成熟度与未验范围 | [`CAPABILITY_MATURITY.md`](CAPABILITY_MATURITY.md) |
| 当前状态与待办（唯一真源） | [`TASK_ACCEPTANCE_LEDGER.md`](TASK_ACCEPTANCE_LEDGER.md) |
| 发布前必须满足的条件 | [`RELEASE_ACCEPTANCE_CHECKLIST.md`](RELEASE_ACCEPTANCE_CHECKLIST.md) |
| 组件交付通道的现状 | [`COMPONENTS.md`](COMPONENTS.md) |
| 测试范围与结论 | [`TEST_REPORT.md`](TEST_REPORT.md) |
| 生态观察与融合研究 | [`ECOSYSTEM_OBSERVATION.md`](ECOSYSTEM_OBSERVATION.md)、[`RESEARCH_AND_FUSION.md`](RESEARCH_AND_FUSION.md) |

## 原文里仍然有效的三条边界（提炼，不是改写）

**① 测量只说明范围，不表示成效。**
OCR 组件约 43.76 MiB 压缩 / 92.72 MiB 安装；相同 PNG 单次直接/组件路径约
3.70/4.87 秒、202.82/205.95 MiB 总进程树 RSS —— **没有总体节省内存的结论**。
固定 HTTP 样本吞吐约 30.33/30.30 页每秒，**无提速证据**。

**② 未验证的仍然未验证。**
OCR 组件是 Windows 开发试点，使用隔离测试公钥，不是正式市场签名发布；
完整 DLL 许可归属、干净机器断网、搬迁、三平台发布与主程序便携包大小未验证；
真实网站登录、外部模型费用、完整人工走查未测。

**③ 一个未关闭的根因。**
单进程合跑单元与集成时 Qt 样式初始化发生 Windows access violation，
后续改用独立进程分组完成验证；**共享 Qt 状态污染只是推测**，确切根因未关闭。
Qt 截图为实际 offscreen 窗口，不等同于完整人工桌面走查。

## 证据位置

完整结果、复现入口、失败处理与证据清单见工作区
`user-test-runs/comprehensive-optimization-20261004/验收报告.md`；
插件/模板开发位于 `market-plugin-and-template-development/comprehensive-20261004`。
