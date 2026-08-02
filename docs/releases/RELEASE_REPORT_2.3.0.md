# OmniCrawler 2.3.0 发布报告

**发布日期**: 2026-07-26
**基线版本**: 2.2.0
**发布类型**: 优化迭代 (非功能性变更)

## 概述

2.3.0 在 2.2.0 性能优化基础上，聚焦安全加固、GUI 架构改善、代码标准化和工程硬化。公共 API 语义不变，仅内部实现路径优化和文档补充。

## 关键变更

### 安全加固
- DuckDB 导出器新增列名白名单校验，防止 SQL 注入通过动态列名绕过。
- Egress Broker 安全测试补强：新增凭据作用域、熔断器、域名策略边界测试套件。
- Pipeline 核心路径安全测试补强：覆盖九阶段编排异常隔离、单 URL 失败不拖垮 run 级不变量。

### GUI 巨型文件拆分
- `gui/main.py` 从 2730 行拆分为 1666 行 + 8 个 delegate 类，减少 39%。
- 采用 `__getattr__` 透明转发模式的 `_BaseDelegate` 基类。
- 47 个 thin forwarder 方法保留在 MainWindow 中，确保 Qt 信号连接不受影响。
- 修复 5 处 F823 `_` 变量遮蔽 i18n `_()` 函数错误。

### 代码标准化
- ruff UP 规则全量迁移：235 处类型注解迁移到 Python 3.10+ 风格，覆盖 63 个文件。
- F401 未使用导入清理：152 处移除。
- 修复 9 处 ruff 残留错误（F821、B023、N812、B008、E741）。
- ruff 最终结果：0 violations。

### 文档与工程
- SDK 公共 API docstring 补充（validate/compile/run/query + 6 个 Protocol 类 + DatasetReader）。
- 架构核心模块 docstring 补充（Pipeline/EgressBroker/RunRepository/TaskIR/BrowserAction）。
- 新增 CODEOWNERS、CONTRIBUTING.md、ADR 模板、pre-commit hooks。
- mypy 配置更新：GUI 模块纳入检查范围（Phase 1 宽松规则）。
- 覆盖率门禁从 70% 提升至 72%。

### 测试补强
- 新增 `test_state_batch.py`：28 个 StateStore 批量操作测试。
- 新增 `test_pipeline_security.py`：Pipeline 九阶段编排安全测试。
- 新增 `test_egress_security.py`：Egress Broker 安全策略测试。
- 新增 `test_benchmarking.py`：性能基准框架测试（20 个）。

## 验证

- 300+ passed（+48 新测试）
- 覆盖率 ≥72%
- mypy, ruff, compileall 通过
- 无新增外部依赖

## 兼容性

- 所有 2.2.0 配置文件无需修改即可使用
- 公共 API 语义不变
