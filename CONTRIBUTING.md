# OmniCrawler 贡献指南

感谢你对 OmniCrawler 项目的关注！本文档描述了参与开发需要遵循的规范和流程。

## 快速开始

```bash
# 克隆仓库
git clone <repo-url> && cd OmniCrawler

# 安装开发依赖
pip install -e ".[full,dev]"

# 安装 pre-commit hooks
pip install pre-commit && pre-commit install

# 运行测试
pytest

# 运行质量门禁
ruff check src/ && ruff format --check src/
mypy src/omnicrawl/ --exclude 'src/omnicrawl/(gui|pdfx|apps)/'
python -m compileall src/ -q
```

## 质量门禁（红线）

所有提交必须通过以下门禁，任何一项不通过即不可合入：

| 门禁 | 阈值 | 说明 |
|------|------|------|
| ruff | 0 violations | 代码风格 + 导入排序 |
| mypy | 0 errors | 类型检查（GUI/pdfx/apps 暂排除） |
| compileall | 0 errors | 语法编译检查 |
| 覆盖率 | >= 66% | 全源码覆盖率，并通过分组门禁（下一目标 70%，长期目标 80%） |
| pytest | 全部通过 | 229+ 测试用例 |

pre-commit hooks 会在提交时自动运行 ruff 和 mypy，从源头防止退化。

## 架构原则（宪法）

以下 6 条原则不可违反，触及前必须先说明影响并征得确认：

1. **分层隔离** — 按"差异来源"分层（source/template → fetcher → parser/extractor → transformer → exporter）
2. **Pipeline 只编排** — 九阶段管线只编排不干活，具体能力下沉到组件
3. **统一入口** — 所有外部调用经 ApplicationService，返回 dict DTO
4. **配置先编译为 Task IR** — 不得新增"跳过 IR 直接跑"的捷径
5. **持久化走 Repository Port** — 上层不得直接 import sqlite3
6. **异常隔离** — 单 URL 失败不拖垮整轮 run

详见项目根目录 `project-instructions`。

## 提交流程

1. **创建分支**：`feat/`、`fix/`、`refactor/`、`docs/`、`test/` 前缀
2. **编写代码**：遵循类型注解规范（Python 3.10+ 风格：`str | None` 而非 `Optional[str]`）
3. **补充测试**：新增功能必须带对应测试；可选/重型能力用 skip 优雅降级
4. **本地验证**：运行 `pre-commit run --all-files` 确保门禁全绿
5. **提交 PR**：PR 描述包含变更摘要、测试方式、是否触及架构原则
6. **代码审查**：至少 1 人 approve 后合入；架构变更需 2 人 approve

## PR 行为约束

- **PR <= 400 行**（目标占比 >80%），大型重构拆分为多个 PR
- **不跳过 hooks**（不使用 `--no-verify`），除非用户明确要求
- **不引入未使用导入**，不吞咽异常
- **日志用英文**，GUI 用户提示用中文，异常消息用英文

## 测试规范

- 测试框架：pytest
- 测试目录：`tests/`，与 `src/` 结构对应
- 命名：`test_<module>.py`，测试函数 `test_<behavior>`
- 因环境缺失依赖而不可跑的测试用 `skip`（带原因），不要注释掉或删除
- 核心模块覆盖率目标 >= 85%

## 文件落点速查

| 你要做什么 | 放在哪里 | 不放在哪里 |
|-----------|---------|-----------|
| 某站点的特殊字段 | source/template 层 | 通用 parser |
| HTTP/浏览器/流式差异 | fetcher 层 | pipeline |
| PDF/OCR/语法解析 | parser/extractor/processor | fetcher |
| 字段清洗/归一化 | transformer | exporter |
| 输出格式（CSV/JSON/DB） | exporter | transformer |

## ADR（架构决策记录）

重要架构决策请记录到 `docs/adr/` 目录，使用模板 `docs/adr/0000-template.md`。

ADR 内容包括：上下文、决策、替代方案、后果。一旦写入不可修改（只能标记为 Superseded）。
