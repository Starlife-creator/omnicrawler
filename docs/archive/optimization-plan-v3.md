# OmniCrawler 综合优化方案 v3

> 版本：3.0 | 日期：2026-07-27 | 基于 v2.3.1 完成状态

---

## 一、现状基线

### 已完成（v2.2.0 → v2.3.1）

| 领域 | 完成项 | 关键指标 |
|------|--------|---------|
| 安全 | DuckDB 列名白名单 | P0 清除 |
| GUI 架构 | main.py 2730→1666 行 + 8 delegate 类 | -39% 体积 |
| 代码规范 | ruff UP 全量迁移 63 文件 | 0 violations |
| 类型检查 | mypy GUI Phase 2 (gui/core strict) | 7 模块零错误 |
| 测试 | +48 新测试 (StateStore/benchmarking/utils) | 356 passed |
| 工程化 | CODEOWNERS/CONTRIBUTING/ADR/pre-commit | CI 硬化 |
| CLI | benchmark 子命令 | 性能基准集成 |
| 覆盖率 | 门禁 70%→72% | 阶梯提升中 |

### GUI 前端优化（7 模块全部完成）

| 模块 | 交付 |
|------|------|
| A. 图标系统 | IconRegistry + 16 SVG icons |
| B. 国际化 | 556 strings .pot + EN .po(73) + msgfmt 编译 |
| C. 令牌完善 | 3 处硬编码清除 + 表单 QSS 重构 |
| D. 动效系统 | MotionSignal 总线 + 2 组件去轮询 |
| E. 组件+A11y | 全局焦点框 + 5 Wizard ARIA |
| F. 视觉回归 | Pillow 快照测试框架 |
| G. 渲染性能 | QSS 缓存(tk_hash) + LogConsole 行限制 |

### 质量门禁

| 指标 | 值 |
|------|-----|
| ruff violations | 0 |
| mypy (non-GUI) | 基线就绪 |
| mypy (gui/core) | strict 通过 |
| 测试 | 356 passed / 23 skipped |
| 覆盖率门禁 | 72% |

---

## 二、残余问题（架构审计 10 维度）

### 按影响与风险分级

```
影响 ↑
HIGH │  [1] 配置双源  [2] CLI 巨型化  [3] 测试组织混乱  [4] 边界渗透
MED  │  [5] __init__.py  [6] pipeline 文档不一致
LOW  │  [7] browser_fetcher 宽带 catch  [8] 入口点残留 main 块
     └────────────────────────────────────────────────────────→ 风险
       低                                      中                  高
```

已排除的高风险项：
- **pipeline 星型→分层重构**（高风险，取消——改文档即可）
- **统一配置模型合并**（中高风险，降级——改双向转换代替合并）

---

## 三、优化方案（3 Phase × 6 任务）

### Phase 1：零风险速赢（P0，0.5 天）

#### 任务 1.1：提升 `runtime_paths` 出 GUI 层

**现状**：`cli.py` 和 `commands/components.py` 从 `gui.runtime_paths` 导入。核心 CLI 依赖 GUI 模块——架构层面的边界污染。

**方案**：
1. `git mv src/omnicrawler/gui/runtime_paths.py src/omnicrawler/runtime_paths.py`
2. 更新 3 处导入：`cli.py`、`gui/main.py`、`commands/components.py`
3. `runtime_paths.py` 本身零 Qt 依赖，纯路径计算

**风险**：🟢 极低（2 行导入路径修改）

#### 任务 1.2：测试目录分层重组

**现状**：`tests/` 下 70 个文件平铺，~25 个带 `_v112`/`_v210` 版本后缀。

**方案**：
1. `git mv` 按功能域分组：
   ```
   tests/
   ├── unit/
   │   ├── config/      # test_config, test_config_migration, test_config_history, ...
   │   ├── pipeline/    # test_pipeline_core, test_pipeline_security, ...
   │   ├── state/       # test_state_store, test_state_batch, ...
   │   ├── egress/      # test_egress, test_egress_security, ...
   │   ├── extraction/  # test_extractors, test_field_designer, ...
   │   ├── template/    # test_template_catalog, test_template_recommend, ...
   │   └── utils/       # test_utils, test_retry, test_benchmarking, ...
   ├── integration/
   │   ├── cli/         # test_cli_workflows_v112, test_cli_*
   │   └── browser/     # test_browser_contract_v112, test_strengthened_features
   └── gui/
       └── visual/      # (已有)
   ```
2. 批量去掉版本后缀（`_v112`→空, `_v210`→空），版本号是历史痕迹不是功能标识
3. 每个新目录添加 `__init__.py`
4. `pytest.ini` / `pyproject.toml` 中 `testpaths` 更新

**风险**：🟢 极低（纯 `git mv`，无逻辑变更）

---

### Phase 2：低风险改进（P1，1 天）

#### 任务 2.1：包级 `__init__.py` 补齐

**方案**：对标 `sdk/__init__.py` 标准（`__all__` + 稳定性标记），为以下包添加公开 API 面：

```python
# src/omnicrawler/__init__.py 增量
__all__ = ["__version__", "AppConfig", "Pipeline", "StateStore", "run_task"]
from .config import AppConfig
from .pipeline import Pipeline
from .state import StateStore
```

```python
# src/omnicrawler/commands/__init__.py
__all__ = ["run_task", "doctor", "export_all", "validate_config", "run_sample"]
```

```python
# src/omnicrawler/apps/__init__.py
__all__ = ["run_field_extractor", "run_pdf_processor"]
```

pdfx 子包需要 `try/except ImportError` 守卫（pdfx 是可选依赖）。

**风险**：🟢 低（纯新增，不影响现有导入）

#### 任务 2.2：CLI 显式命令注册表

**方案**：不用装饰器（避免 import 时机问题），用显式字典：

```python
# src/omnicrawler/cli/_commands.py
COMMANDS: dict[str, dict] = {
    "run":        {"module": ".commands.run_task",   "function": "run_task",   "help": "启动任务"},
    "resume":     {"module": ".commands.run_task",   "function": "resume_task","help": "继续任务"},
    "validate":   {"module": ".commands.run_task",   "function": "validate",   "help": "校验配置"},
    "sample":     {"module": ".commands.run_sample", "function": "run_sample", "help": "小样本试跑"},
    "benchmark":  {"module": ".commands.benchmark",  "function": "run_benchmark", "help": "性能基准"},
    # ... 其余 ~25 个命令
}
```

`cli.py` 中：
- `_add_arguments()` 遍历 `COMMANDS` 自动生成 argparse
- `_dispatch()` 替换 31 个 `if/elif` 为 `importlib.import_module(COMMANDS[cmd]["module"]).func(args)`

**收益**：cli.py 928→~400 行，新增命令只需加一行字典条目。

**风险**：🟡 中
- 嵌套 subparser 命令（templates、schedule、backup）需要两级字典
- 确保 `importlib` 延迟导入不影响启动性能

#### 任务 2.3：架构文档修正

**方案**：更新 `docs/architecture.md`（如存在）或项目 README，将：

> "五层线性管道 source_diff→transport→syntax→cleaning→delivery"

修正为：

> "以 Pipeline 为核心的星型编排器 + 九阶段执行流：plan→discover→fetch→parse→filter→quality→export→archive→cleanup。各阶段通过 StateStore 共享状态，Pipeline 负责阶段间异常隔离和回滚决策。"

**风险**：🟢 极低（仅文档变更）

---

### Phase 3：中风险改进（P2，3-5 天，需决策）

#### 任务 3.1：配置双向转换层（降级替代合并）

**方案**：不合并 `AppConfig` 和 `CrawlConfig`。在 `gui/core/config_model.py` 中添加：

```python
class CrawlConfig:
    def to_app_config(self) -> AppConfig:
        """将 GUI 视图转为运行时核心配置。"""
        ...

    @classmethod
    def from_app_config(cls, app_config: AppConfig) -> CrawlConfig:
        """从运行时核心配置重建 GUI 视图。"""
        ...
```

此时 `CrawlConfig` 作为 `AppConfig` 的 **View/Adapter**，而非独立的重复模型。

**前置工作**（2h）：输出两个模型的字段差异表（GUI 专属 vs 核心专属 vs 共享字段），附在 ADR 中。

**风险**：🟡 中高（需要理解两套模型的每个字段语义，遗漏字段会导致运行时数据丢失）

#### 任务 3.2：browser_fetcher.py 拆分

**方案**：748 行 → 拆为 3 个文件：
- `browser_engine.py`：`BrowserAction` + `BrowserEngine` Protocol（~120 行）
- `browser_fetcher.py`：`BrowserFetcher` 核心逻辑（~350 行）
- `browser_stealth.py`：反检测/指纹伪装逻辑（~250 行）

`browser_engine.py` 已在 2.2.0 定义——需要的是物理文件分离。

**风险**：🟡 中（12 个宽带 `except Exception` 需要逐个审查，避免拆分时引入作用域 bug）

---

## 四、路线图

```
Week 1（本次）
├── Phase 1.1  runtime_paths 提升      [1h]
├── Phase 1.2  测试目录重组            [2h]
├── Phase 2.1  包级 __init__.py 补齐   [2h]
└── Phase 2.3  架构文档修正            [0.5h]

Week 2（下次 session）
└── Phase 2.2  CLI 注册表模式          [1 天]

Week 3-4（需决策）
├── Phase 3.1  配置双向转换 (先做差异表) [3-5 天]
└── Phase 3.2  browser_fetcher 拆分     [2-3 天]
```

---

## 五、效果预估

| 指标 | 当前 | Phase 1+2 完成后 | Phase 3 完成后 |
|------|------|-----------------|---------------|
| cli.py 行数 | 928 | ~400 | ~400 |
| 测试目录杂乱度 | 70 文件平铺 + 25 版本后缀 | 8 子目录, 0 版本后缀 | 同左 |
| 包级 API 发现性 | 仅 sdk/pipeline 可发现 | 全 7 包可发现 | 同左 |
| 跨层导入数 | 2 处 (gui→cli) | 0 | 0 |
| 配置模型同步成本 | 新增字段改 2 处 | 新增字段改 2 处 | 新增字段改 1 处（通过转换层） |
| 架构文档 vs 代码一致性 | 不一致 | 一致 | 一致 |
| ruff | 0 | 0 | 0 |
| 测试 | 356 passed | 356 passed | 356 passed |

---

## 六、ADR 摘要

| 决策 | 结论 | 理由 |
|------|------|------|
| Pipeline 星型编排器是否重构 | 不重构 | 30+ 依赖是刻意设计的编排器，重构风险高且无功能收益 |
| AppConfig/CrawlConfig 是否合并 | 不合并 | 改为双向转换层，避免 GUI 全量回归 |
| CLI 注册表用装饰器还是字典 | 字典 | 避免 import 时机问题，更可控 |
| 测试版本后缀是否保留 | 删除 | 版本号是迭代历史，不应是永久命名 |

---

*本文档将存入 `docs/architecture/optimization-plan-v3.md`*
