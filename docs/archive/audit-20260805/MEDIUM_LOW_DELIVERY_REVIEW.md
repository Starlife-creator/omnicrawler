# 审计 medium/low 复核：只核「影响正确交付」的条目（W6.4）

> 结论日期：2026-09-16 · 对应《优化方案》§6.4 **W6.4**（§5.5 #18）
> 本文件是**筛选复核**，不是全量复核。判据只用三类：**数据正确性 / 权限 / 输出保护**。

## 为什么不做全量

`docs/archive/audit-20260805/` 共 **67 条** medium/low（medium 23 / low 44）。
方案明确要求只核「影响**正确交付**」的条目 —— 全量复核会把精力摊薄在
性能、可读性、资源预算等**不影响交付正确性**的问题上，而那些更适合随相关改动处理。

## 筛选规则（可复核）

按报告里的条目标题与正文，纳入满足任一条件的条目：

* **数据正确性**：会不会产出**错误/被污染/丢类型**的数据，或让错误输入静默通过；
* **权限**：越权、路径逃逸、凭据/脱敏语义被误用；
* **输出保护**：产物**缺失/损坏/被覆盖**而无提示，或中断后留下半成品。

**排除**（附理由，不做"顺手也改了"）：
性能与索引（`responses` 索引不足、`review_queue` 全表扫描、规则表无限增长）、
资源预算（`max_pages` 超预算语义）、可读性/内部实现（私有属性访问、报错文案缺字段）——
这些**不影响交付是否正确**，属另一主题（性能优化排在质量收口之后，方案已如此安排）。

## 逐条结论（纳入项）

| # | 条目（报告） | 类别 | 结论 | 证据 |
|---|---|---|---|---|
| 1 | `exporters.py:66-70` 扁平化键与基础字段同名互相覆盖 | 数据正确性 | **本次修复** | 改为「改名保留」：冲突键写 `data.<key>`，并汇总一条 `warnings`；`tests/unit/audit/test_medium_low_correct_delivery.py::test_data_keys_do_not_overwrite_base_columns`（**承重性**：打回旧行为 ⇒ 判红） |
| 2 | `_exports.py:87` 汇总阶段 `int(endpoints)` 可被插件值炸掉 | 数据正确性 | **本次修复** | 新增 `_as_int()` 宽容取整（非数字/布尔 ⇒ 0）；同文件 `test_endpoint_sum_tolerates_non_numeric_values` |
| 3 | `plan_compiler.py:38` seeds 用 `str()` 强转 | 数据正确性 | **本次修复** | 非字符串元素**显式拒绝**（`ValueError`），不再产出 `{'url': …}` 这类垃圾种子；同文件 `test_plan_compiler_rejects_non_string_seeds` |
| 4 | `change_detector.py:440-446` `save_rules` 非原子写 | 输出保护 | **本次修复** | 改用既有 `core.utils.atomic_write`（临时文件 + fsync + 原子替换）：中断不再留半个 JSON；同文件两条（行为 + 源码守卫） |
| 5 | `pdf_integration.py:27-28` 复用已存在 project.yaml 不校验 | 输出保护 | **本次修复** | 复用 `pdfx.config.load_config` 做 **schema 级**校验，损坏时给出可执行提示；同文件 `test_pdf_project_config_is_validated_before_reuse` |
| 6 | `exporters.py:150-153` Parquet 全 `str` 化丢类型 | 数据正确性 | **已闭环**（早期批次） | `exporters.py:215` 注释「S3.4.1 ③：parquet 保留原始类型（不再全 str 化），pyarrow 自动推断」 |
| 7 | `exporters.py:112-118` openpyxl 缺失时静默跳过 xlsx | 输出保护 | **已闭环**（早期批次） | `exporters.py:166` 「S2.5.24：xlsx 缺 openpyxl 显式告警（与 parquet/duckdb 一致）」 |
| 8 | `exporters.py:80` JSONL 同时含 `data_json` 与嵌套 data | 数据正确性 | **已闭环**（早期批次） | `exporters.py:109` 「S3.4.1 ①：同一份数据不写两遍——剔除 data_json/evidence_json」 |
| 9 | `pdf_integration.py:69` 只扫顶层 `*.pdf` | 输出保护 | **已闭环**（早期批次） | `pdf_integration.py:85` 「S2.3.5：子目录 PDF 也计入（rglob 递归）」 |
| 10 | `plan_compiler.py:79-84` `plan_hash` 基于脱敏配置 | 权限（审计语义） | **转为文档约束** | `plan_hash` 的语义是**脱敏后**的计划指纹；脱敏是有意为之（凭据不进哈希/不进日志）。已在代码注释中说明用途边界，不改变行为 —— 若将来用它做"敏感值变更检测"，需另存指纹，属**另一项功能**。 |
| 11 | `state_store.py` `recover_incomplete_runs` 无条件重置所有 `in_progress` | 数据正确性 | **不予修改**（附理由） | 触发条件是"同一工作区并发多 worker"，而本项目**单机桌面 / 单用户**、工作区由一次运行独占（方案锚①）。同工作区并发运行会在更早的状态机层被拒绝；此处改成按 run 过滤会掩盖"配置被误用"的事实。**推翻条件**：若将来支持同工作区并发（多 worker/多实例），此条必须重开并加并发用例。 |

## 证据汇总

* 本次 5 处修复 + 6 条验收用例：`tests/unit/audit/test_medium_low_correct_delivery.py`（**6 passed**）；
* 承重性核对：把「改名保留」打回旧行为 ⇒ 判红（已还原复验）；
* 全量门禁：`tools/self_verify.py --set static` 16/16；`mypy src/omnicrawler` 408 文件零问题。

## 本次**不做**的事（明确边界）

* 不追全量 67 条 —— 排除项的理由见上；
* 不改 `plan_hash` 行为（属新功能，不是修缺陷）；
* 不改并发恢复语义（与项目锚"单机单用户"一致；已写明推翻条件）。
