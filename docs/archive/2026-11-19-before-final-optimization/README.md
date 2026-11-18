# 2026-11-19 最终优化前快照

> **本目录内容已冻结，描述的是 2026-11-19 之前的规划状态，不代表当前行为。**

## 为什么归档

本轮（2026-11-18 ~ 11-19）实施了《OmniCrawler 最终统一优化方案》的五个批次，
方案文档本身因此成为**已完成的历史规划**。按归档只读纪律：

- 原文快照保存在本目录，**逐字节保留**，供追溯当时的判断依据；
- 根目录的现行版本改为**索引页**，指向本快照；
- 文中「待做」「计划」等表述只代表当时状态，不因归档升级为已完成。

## 归档内容

| 文件 | 说明 |
|---|---|
| `OmniCrawler最终优化方案_2026-11-18.md` | 五批次方案原文（合并自 `omnicrawler-unified-edition-components-mirrors-plan.md` + 本仓实施记录） |

## 本轮实际完成了什么

| 批次 | 内容 | 状态 |
|---|---|---|
| 2 | 浏览器内核可替换（`BrowserRuntimeSpec` + 参数隔离）、登录态 `engine_family` 绑定、跨内核复用提示 | 已实施 |
| 3 | Wheel-Pack 子进程运行器、`ocr-paddle` 配方 | 已实施 |
| 4 | 市场三栏多源聚合、换源安全判据、索引新鲜度、四类下载通道 | 已实施 |
| 5 | 停发不停建、Standard↔Full 能力等价性对账 | 已实施 |

## 原文中与实测不符的三处（已在本轮核实）

方案把以下三项列为「待做」，实测**早已存在**：

| 方案原文说 | 实测 |
|---|---|
| 需落地 `browser-chromium-bundled` 预置组件登记 | `capability_slots.py` 已注册 playwright / firefox / webkit / selenium 四家，含 `bundled: True` |
| 需打通任务画布内核选择 | `task_canvas_draft.py` 已有 `slot_engine` 下拉框，数据源 `slot_combo_items("browser")` |
| 需补齐 spec 的 hiddenimports | spec 用 `collect_submodules("omnicrawler")` 自动收集，无需手工列举 |

## 尚未验证的部分（只能由 CI 确认）

- 三平台 PyInstaller 构建是否成功
- Standard 与 Full 的**实际能力集**是否等价

对账链路的**前置条件**（能否取到两份报告、比较的是否为应比较的项、失败是否被吞）
已在本机逐项验证，但这不等于等价性本身已被证明。

## 阅读现行文档

见 [`docs/README.md`](../../README.md) 第 2 节的现行文档索引。
