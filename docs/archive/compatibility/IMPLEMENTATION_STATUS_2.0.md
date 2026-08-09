# OmniCrawler 2.0实施状态

主线：桌面专业版优先，简单模式低门槛且能力完整，公共SDK和插件体系持续成长。

| 里程碑 | 状态 | 说明 |
|---|---|---|
| 1.1.1 安全与发布修复 | Implemented / external gates pending | 本地与真实浏览器门禁通过；Docker CI和最终便携包待外部构建环境 |
| 1.1.2 质量门禁与回归基线 | Implemented / external gates pending | 150项回归及全源码分组覆盖率门禁通过；Docker、跨系统CI矩阵和最终便携包待外部环境 |
| 1.2.0 统一安全出口与运行可靠性 | Implemented / external gates pending | 169项回归、真实浏览器、覆盖率、Wheel与SBOM本地通过 |
| 1.3.0 Task IR与应用服务 | Implemented / external gates pending | 177项回归、计划CLI、覆盖率、Wheel与SBOM本地通过 |
| 1.4.0 桌面Worker与便携交付 | Implemented / external gates pending | 185项回归、认证IPC、覆盖率、Wheel/SBOM本地通过 |
| 1.5.0 简单模式与帮助体系 | Implemented / external gates pending | 199项回归；首页、快速任务、帮助、离线演示和无障碍本地通过 |
| 1.6.0 专业版与开发者预览 | Implemented / external gates pending | 204项回归；复核台、SDK、插件隔离、AI边界和开发者检查器通过 |
| 1.7.0 证据、契约和时态事实 | Implemented / external gates pending | 证据哈希链、回放、Schema契约、治理和时态事件专项通过 |
| 1.8.0 影子修复和自适应执行 | Implemented / external gates pending | 影子候选、人工批准/回滚、自适应审计和反馈语料专项通过 |
| 1.9.0 Benchmark、生态与RC | Implemented / external soak gates pending | 语料规模、Benchmark、指标、生态撤回和金丝雀策略专项通过 |
| 2.0.0 GA | Source complete / coverage & external gates pending | 216项回归；总覆盖67.11%、浏览器/API 69.92%，尚未宣称GA门禁全通过 |
| 2.1.0 GUI舒适度与最终本机收口 | Implemented / local release verified | 统一主题、背景动效、可访问性、CSV修复；覆盖门禁、Standard/Full便携构建和离线运行时烟雾测试通过 |

每个版本必须同步更新：`CHANGELOG.md`、发行报告、`docs/TEST_REPORT.md`、迁移/兼容说明和本状态表。

当前执行检查点：2.1.0功能、文档、本机覆盖率、Wheel、SBOM、Source ZIP及Windows Standard/Full
便携制品已经完成。没有外部执行证据的Docker、正式代码签名、跨操作系统矩阵和物理机长稳仍保持pending，
不作为已通过项。
