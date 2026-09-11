"""质量门禁的**唯一清单**——本地自证与 CI 都以此为据。

## 为什么需要这个文件

门禁散落在各 workflow 的步骤里时，文档与 CI 会各自漂移。实测（2026-09-11）：

* 仓库共有 **14** 个 `tools/check_*.py` 门禁，CI 全都跑了；
* 而 `CONTRIBUTING.md` 的「质量门禁（红线）」表只列了 ruff / mypy / compileall / 覆盖率 / pytest
  ——照着文档本地验证，会**漏掉 9 项**；
* 本地也没有任何聚合入口，于是「自证」只能凭记忆挑几个跑。

清单集中到这里之后：

* :mod:`tools.self_verify` 按清单执行并产出证据报告；
* `tests/unit/tools/test_gate_registry.py` 断言清单与 workflow **双向一致**——
  漏登记或漏接线都会让测试失败，漂移不可能再悄悄发生。

## 集合（`sets`）的含义

| 集合 | 何时能跑 | 说明 |
|---|---|---|
| `static` | 干净的开发检出即可 | 红线的核心：不依赖构建产物、不依赖覆盖率数据 |
| `tests` | 同上（较慢） | 测试套件本身 |
| `coverage` | 先跑一次覆盖率统计 | 需要 `coverage.json` |
| `install` | 逐 profile 安装后 | 依赖矩阵，CI 用 matrix 提供参数 |
| `release` | 有构建产物后 | 需要 SBOM / 平台产物路径 |
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Gate:
    """一条可执行门禁。

    Attributes:
        name: 稳定标识（出现在报告与文档里）。
        args: 传给解释器的固定参数（``python <args>``）。
        sets: 所属集合（见模块文档）。
        description: 这条门禁在防什么。
        script: ``tools/check_*.py`` 的相对路径；非空则参与「与 CI 双向一致」的校验。
        requires_file: 运行前必须存在的文件；缺失则**显式跳过**并说明原因。
        needs_args: 必须由调用方在运行期提供的参数名；非空表示本地自证无法直接执行。
        ci_note: 与 CI 的差异说明——如实写，不掩盖。
    """

    name: str
    args: tuple[str, ...]
    sets: frozenset[str]
    description: str
    script: str | None = None
    requires_file: str | None = None
    needs_args: tuple[str, ...] = ()
    ci_note: str = ""


_STATIC = frozenset({"static"})

GATES: tuple[Gate, ...] = (
    # ---------- static：红线的核心 ----------
    Gate(
        name="compileall",
        args=("-m", "compileall", "-q", "src", "tests", "examples"),
        sets=_STATIC,
        description="语法编译检查（含示例）",
    ),
    Gate(
        name="ruff",
        args=("-m", "ruff", "check", "src", "tests", "tools"),
        sets=_STATIC,
        description="代码风格与导入排序",
        ci_note="CI 同步为 src tests tools；纳入 tools 使本地与 CI 完全一致（此前 CI 只查 src tests）。",
    ),
    Gate(
        name="mypy",
        args=("-m", "mypy", "src/omnicrawler"),
        sets=_STATIC,
        description="类型检查（与 CI 同一范围）",
    ),
    Gate(
        name="templates_validate",
        args=("-m", "omnicrawler.cli", "templates", "validate"),
        sets=_STATIC,
        description="内置模板可加载、可校验",
    ),
    Gate(
        name="check_coding_standards",
        args=("tools/check_coding_standards.py", "src"),
        sets=_STATIC,
        description="编码规范（命名、行尾无关的结构约束等）",
        script="tools/check_coding_standards.py",
    ),
    Gate(
        name="check_architecture",
        args=("tools/check_architecture.py",),
        sets=_STATIC,
        description="架构原则（分层、导入环、预算）",
        script="tools/check_architecture.py",
    ),
    Gate(
        name="check_sdk_api",
        args=("tools/check_sdk_api.py",),
        sets=_STATIC,
        description="SDK 对外 API 契约",
        script="tools/check_sdk_api.py",
    ),
    Gate(
        name="check_cli_docs",
        args=("tools/check_cli_docs.py",),
        sets=_STATIC,
        description="CLI 命令与文档一致",
        script="tools/check_cli_docs.py",
    ),
    Gate(
        name="check_docs_consistency",
        args=("tools/check_docs_consistency.py",),
        sets=_STATIC,
        description="文档导航、版本标记与归档门页",
        script="tools/check_docs_consistency.py",
    ),
    Gate(
        name="check_network_boundaries",
        args=("tools/check_network_boundaries.py",),
        sets=_STATIC,
        description="网络访问边界（出网只能经由受控通道）",
        script="tools/check_network_boundaries.py",
    ),
    Gate(
        name="check_release_integrity",
        args=("tools/check_release_integrity.py",),
        sets=_STATIC,
        description="发布完整性（无产物时可校验仓库侧约束）",
        script="tools/check_release_integrity.py",
    ),
    Gate(
        name="check_lint_budget",
        args=("tools/check_lint_budget.py",),
        sets=_STATIC,
        description="ruff 豁免预算只降不升",
        script="tools/check_lint_budget.py",
    ),
    Gate(
        name="check_gui_conventions",
        args=("tools/check_gui_conventions.py",),
        sets=_STATIC,
        description="GUI 约定（无障碍名 / 内联样式 / 裸色值）",
        script="tools/check_gui_conventions.py",
    ),
    Gate(
        name="check_lockfile_consistency",
        args=("tools/check_lockfile_consistency.py",),
        sets=_STATIC,
        description="依赖锁一致性（pyproject ↔ uv.lock，无需安装 uv）",
        script="tools/check_lockfile_consistency.py",
        ci_note="权威复现由专门的 lockfile CI job 负责（uv lock --check + uv sync --locked）；本门禁在无 uv 环境下也能跑。",
    ),
    Gate(
        name="check_minimal_install",
        args=("tools/check_minimal_install.py",),
        sets=_STATIC,
        description="核心能力不依赖可选 extras",
        script="tools/check_minimal_install.py",
        ci_note="CI 在**最小安装**环境里跑（只 pip install -e .），证据更强；本地通常在完整 dev 环境里跑。",
    ),
    # ---------- tests ----------
    Gate(
        name="pytest",
        args=("-m", "pytest", "-q"),
        sets=frozenset({"tests"}),
        description="测试套件（CI 以 coverage run 形式执行同一入口）",
    ),
    # ---------- coverage ----------
    Gate(
        name="check_coverage_gates",
        args=("tools/check_coverage_gates.py", "coverage.json"),
        sets=frozenset({"coverage"}),
        description="按包 / 关键模块的覆盖率下限与分组门禁",
        script="tools/check_coverage_gates.py",
        requires_file="coverage.json",
    ),
    # ---------- install（CI matrix 提供参数） ----------
    Gate(
        name="check_extra_install",
        args=("tools/check_extra_install.py",),
        sets=frozenset({"install"}),
        description="每个 extras profile 可独立安装且导入完整",
        script="tools/check_extra_install.py",
        needs_args=("profile",),
        ci_note="需逐 profile 安装（CI matrix）；本地自证显式跳过并列出原因。",
    ),
    # ---------- release（需要构建产物） ----------
    Gate(
        name="check_guardrails",
        args=("tools/check_guardrails.py",),
        sets=frozenset({"release"}),
        description="发布护栏（SBOM、许可、来源等）",
        script="tools/check_guardrails.py",
        needs_args=("sbom",),
        ci_note="需先构建产物并生成 SBOM。",
    ),
    Gate(
        name="check_artifact_budget",
        args=("tools/check_artifact_budget.py",),
        sets=frozenset({"release"}),
        description="分发产物体积预算",
        script="tools/check_artifact_budget.py",
        needs_args=("platform", "directory"),
        ci_note="需先构建对应平台产物。",
    ),
)

#: 全部集合名（供命令行校验）。
GATE_SETS: tuple[str, ...] = ("static", "tests", "coverage", "install", "release")


def gates_for(sets: frozenset[str] | set[str] | tuple[str, ...]) -> tuple[Gate, ...]:
    """返回属于任一所给集合的门禁，保持 :data:`GATES` 的顺序。"""
    wanted = set(sets)
    unknown = wanted - set(GATE_SETS)
    if unknown:
        raise ValueError(f"未知门禁集合: {sorted(unknown)}（合法值: {list(GATE_SETS)}）")
    return tuple(gate for gate in GATES if gate.sets & wanted)


def ci_managed_scripts() -> frozenset[str]:
    """返回参与「与 CI 双向一致」校验的脚本集合。"""
    return frozenset(gate.script for gate in GATES if gate.script)
