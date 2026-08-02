# ADR-0001: DuckDB Column Name Whitelist Validation

## Status

Accepted

## Context

OmniCrawler 2.2.0 代码审查发现 `exporters.py` 的 DuckDB 导出路径中，`CREATE TABLE` 的列名直接来自数据记录的键（`headers`），仅通过双引号转义后拼入 DDL。虽然转义符合 SQL 标识符规范，但缺少白名单校验意味着安全防线是"补丁式"的——依赖开发者记得转义，而非依赖类型系统或验证层保证安全。

如果上游数据被攻击者控制（例如 API 返回的 JSON 键名包含恶意 SQL 片段），虽然概率低，但一旦成功影响半径是整个 DuckDB 文件。

## Decision

在 DuckDB 导出路径中增加正则白名单校验 `^[a-zA-Z_][a-zA-Z0-9_]*$`，拒绝不符合规范的列名并抛出 `ValueError`。

校验函数 `_validate_column_names()` 放在 `exporters.py` 模块级别，在 DDL 生成前调用。

## Alternatives Considered

### Alternative 1: 仅依赖双引号转义（维持现状）
- **优点**: 零改动，不破坏现有数据
- **缺点**: 安全防线依赖开发者纪律，不是架构保证
- **不选的原因**: 审查评级为 P0，安全防线应从"补丁式"升级为"契约式"

### Alternative 2: 对不合规列名进行自动 sanitize（如中文→拼音/序号）
- **优点**: 不会拒绝导出，用户体验更好
- **缺点**: 自动转换可能引入数据语义歧义；增加复杂度
- **不选的原因**: 2.3.0 阶段先确保安全边界，自动 sanitize 作为后续增强

## Consequences

### Positive
- DuckDB 导出路径有了显式的验证边界，安全防线从"补丁式"升级为"契约式"
- 异常列名在导出阶段被拦截，不会传播到数据库

### Negative
- 如果现有数据中有不符合白名单的列名（如中文键名），导出会抛出 ValueError
- 需要用户手动调整数据键名或在上游进行 sanitize

### Neutral
- 不影响 CSV/JSONL/XLSX/Parquet 等其他导出格式

## References

- 代码审查报告: omnicrawler-code-review-report.html
- 优化方案: omnicrawler-optimization-plan-v2.html
- 安全审查: F1.1.1.1 (P0)

---

Date: 2026-07-26
Decided by: starlife
