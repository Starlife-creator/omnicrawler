# 架构决策记录（ADR）

> 本目录存放**架构决策记录**。每份 ADR 记录一个已做出的技术决策及其理由，
> 供日后回溯「为什么这样定」，而不是「现在是什么样」。
>
> 现行状态请看 [`docs/TASK_ACCEPTANCE_LEDGER.md`](../TASK_ACCEPTANCE_LEDGER.md)（验收账本）
> 与 [`docs/README.md`](../README.md)（文档导航）。

## 什么是 ADR，什么不是

| 是 | 不是 |
|---|---|
| 一次**决策**的上下文、选项、取舍与结论 | 操作手册（看 `INSTALLATION.md`） |
| 决策**当时**的理由（不随后续修改而改写） | 当前行为描述（看对应现行文档） |
| 可被后续 ADR **取代**（标 `Superseded by`） | 可被随意修订的活文档 |

**只读纪律**：已接受的 ADR 不改写。若决策变了，做法是**新增一份 ADR 标明取代关系**，
而不是回头修改原文——否则「当初为什么这么定」就永久丢失了。

## 现有决策

| 编号 | 标题 | 状态 | 主题 |
|---|---|---|---|
| [ADR-0001](0001-duckdb-column-whitelist.md) | DuckDB 列名白名单校验 | Accepted | 数据层的 SQL 注入防护 |
| [ADR-005](ADR-005-config-field-audit.md) | AppConfig 与 CrawlConfig 字段差异审计 | 已生效（2026-07-27） | 两套配置模型为何**不做合并** |
| [0000-template](0000-template.md) | 模板 | —— | 新建 ADR 的骨架 |

> **编号不连续是正常的**：`ADR-005` 早于 `ADR-0001` 的编号体系建立，
> 为保持既有引用有效，未做重编号。
>
> 同类情况参见归档区 `docs/archive/ADR-001-plugin-catalog.md`（2026-09-20 冻结，
> 已被 `MARKET_ECOSYSTEM.md` / `PLUGIN_CONTRACT.md` 取代）。

## 新增一份 ADR

1. 复制 [`0000-template.md`](0000-template.md) 为 `NNNN-短横线标题.md`；
2. 填 `Status`（`Proposed` / `Accepted` / `Deprecated` / `Superseded by ADR-N`）；
3. 在**上表**登记一行；
4. 若本 ADR 取代了既有决策，在旧 ADR 的 `Status` 里加 `Superseded by` 指向新编号
   （这是「只读纪律」的**唯一例外**：状态行允许更新，正文不改）。

## 判据

新增 ADR 时至少回答三件事：

- **上下文**：遇到了什么问题、有什么约束？
- **决策**：做了什么选择？
- **后果**：代价是什么、放弃了哪些选项？

没有「后果」的 ADR 只是公告，不是决策记录。
