# OmniCrawler 1.3.0 发行报告

发行日期：2026-07-22

## 交付结论

1.3.0 已完成 Task IR v1、确定性 TaskPlan 编译器、计划权限/资源/冲突/解释/差异/哈希、试跑绑定、
Application Service、统一事件 DTO、四类控制器、Repository 端口、九阶段协议和 CLI command 拆分。
v5 YAML、简单 TaskSpec、模板、操作录制与 API 候选可无损进入同一编译器。

## 验证证据

| 门禁 | 结果 |
|---|---|
| 全量 Pytest | 177 passed，2 skipped |
| 真实浏览器与离屏 GUI | 4 passed |
| 全源码覆盖率 | 65.61%，门槛 60% |
| 安全与状态 | 89.47%，门槛 85% |
| 管线、HTTP与来源 | 75.05%，门槛 75% |
| 浏览器与 API | 81.57%，门槛 70% |
| PDF 与 OCR | 66.97%，门槛 65% |
| 桌面核心 | 61.29%，门槛 60% |
| Mypy | 86 个源文件零错误 |
| Ruff | 通过 |
| Python 源码编译 | 148 个文件通过 |
| Task IR JSON Schema | JSON 解析通过 |
| CLI | 1.3.0；plan 编译、输出和帮助契约通过 |

## 制品

- `omnicrawler_platform-1.3.0-py3-none-any.whl`：402,988 字节；SHA-256
  `87cf05cd26472d8b3a973c5fdc5fb0cd530a617032e1962c294e1645a9e3daad`
- `omnicrawler-1.3.0-sbom.cdx.json`：34,965 字节；SHA-256
  `f7d558e3dcfe5bac5331df7fc483541f02059deca760c0333dab11c99666cfaf`

## 兼容与回滚

- 配置协议仍为 v5，插件 API 仍为 v1，Task IR 首版为 v1。
- `TaskSpec` 和原五步向导继续存在；未知字段通过 IR passthrough 与 GUI 深合并保留。
- `ApplicationService` 是内部基线，公共 SDK Preview 延后至 1.6.0。
- 可回滚到 1.2.0 可执行文件；1.3.0 没有破坏性数据库迁移。保留原 YAML 即可重新编译。

## 外部门禁

Docker、Windows Standard/Full 最终便携包和托管跨系统/Python矩阵在当前环境未执行，保持 pending。
