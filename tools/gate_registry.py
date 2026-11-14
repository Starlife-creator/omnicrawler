"""质量门禁的**唯一清单**——本地自证与 CI 都以此为据。

## 为什么需要这个文件

门禁散落在各 workflow 的步骤里时，文档与 CI 会各自漂移。最初的形态是：

* `tools/check_*.py` 门禁数量在文档里写成固定数字，而数字很快就过时了
  （曾经写「19 个」，随后磁盘上实际已是 28 个；「漏掉 13 项」同理）——
  **凡是靠人手同步的计数都会漂移**，所以这里不再写死任何数量；
* `CONTRIBUTING.md` 的「质量门禁（红线）」表只列了 ruff / mypy / compileall / 覆盖率 / pytest，
  照着文档本地验证会漏掉一大批 `check_*.py`；
* 本地也没有任何聚合入口，于是「自证」只能凭记忆挑几个跑。

清单集中到这里之后：

* :mod:`tools.self_verify` 按清单执行并产出证据报告；
* `tests/unit/tools/test_gate_registry.py` 断言清单与 workflow **双向一致**——
  漏登记或漏接线都会让测试失败，漂移不可能再悄悄发生；
* 门禁总数由 :data:`GATES` 与 :func:`ci_managed_scripts` 在运行时给出，
  文档里不重复记账（计数以代码为准，见该测试文件的门禁清单断言）。

## 「跳过（SKIP）」是第三种结局

门禁只有「通过 / 失败」两态时，**环境不具备所需能力**与**代码有问题**会混成同一个红。
实证：Git for Windows 的 bash 在被禁命名管道的沙箱里连启动都不行，`bash -n` 对每个脚本
都报 `couldn't create signal pipe, Win32 error 5`，于是 9 个脚本全被写成「语法错误」——
这是**环境假红**，换到任何受限环境都会误导人。

因此门禁可以**自述跳过**：以 :data:`SKIP_EXIT_CODE` 退出、并在最后一行写清缺的是什么能力；
:mod:`tools.self_verify` 把它记成 `skipped` 而不是 `failed`。三条边界：

1. 只有门禁**自己判定**「能力缺失」才允许用该退出码；参数缺失仍走 `needs_args`、
   前置产物缺失仍走 `requires_file`——这两类在 :mod:`tools.self_verify` 里事先判掉，
   与门禁无关。
2. 跳过**计入 `skipped`、不计入 `passed`**；一次运行全是跳过时结论仍是 `unverified`（非 0）。
3. 退出码是**约定不是豁免**：门禁必须给出可读原因，否则报告里只剩一个数字。

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
        requires_file: 运行前必须存在的**路径**（文件或目录）；缺失则**显式跳过**并说明原因。
        needs_args: 必须由调用方在运行期提供的参数名；非空表示本地自证无法直接执行。
        ci_note: 与 CI 的差异说明——如实写，不掩盖。
        declared_ci_flags: CI 里对这条门禁额外使用的旗标（本地自证不用的那些）。
            这是**声明**而不是豁免：:mod:`tests.unit.tools.test_gate_registry` 会把
            workflow 里出现的旗标与「``args`` 自带旗标 + ``needs_args`` + 本字段」逐条比对，
            多一个少一个都判红。留空表示「CI 与本地完全相同」。
    """

    name: str
    args: tuple[str, ...]
    sets: frozenset[str]
    description: str
    script: str | None = None
    requires_file: str | None = None
    needs_args: tuple[str, ...] = ()
    ci_note: str = ""
    declared_ci_flags: tuple[str, ...] = ()


#: 门禁「环境能力缺失」的约定退出码（与 automake / pytest 的跳过约定同值）。
#: 门禁以该码退出时，:mod:`tools.self_verify` 记 ``skipped`` 而非 ``failed``。
SKIP_EXIT_CODE = 77

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
        name="post_bump_steps",
        args=("tools/post_bump_steps.py", "--check-current"),
        sets=_STATIC,
        description="版本收口：声明「当前版本」的锚点页（releases/archive README）与 pyproject 版本一致",
        script="tools/post_bump_steps.py",
        ci_note=(
            "CI 与本地都用 `--check-current`（只读、不需要 --old/--new）。"
            "`--check --old X --new Y` 是发布后的人工补步模式，两者判据不同。"
        ),
    ),
    Gate(
        name="check_anchor_integrity",
        args=("tools/check_anchor_integrity.py",),
        sets=_STATIC,
        description="锚段完整性：项目根 优化方案.md 的 §一/§1.1/§1.2/§1.3 与登记摘要一致",
        script="tools/check_anchor_integrity.py",
        ci_note=(
            "锚文件在**项目根**（主仓的上一级），CI checkout 拿不到 ⇒ CI 里显式 SKIP"
            "（仍会校验摘要登记表结构）。它守的是维护者本机：根目录不是 git 仓，"
            "改锚不会有任何 diff，摘要变更就是唯一可审痕迹。"
        ),
    ),
    Gate(
        name="check_line_endings_policy",
        args=("tools/check_line_endings_policy.py",),
        sets=_STATIC,
        description="换行规则与 .gitattributes 一致（文本默认 LF，bat/cmd 检出 CRLF）",
        script="tools/check_line_endings_policy.py",
    ),
    Gate(
        name="check_shell_scripts",
        args=("tools/check_shell_scripts.py",),
        sets=_STATIC,
        description="根目录 shell/command 脚本语法、失败保护、venv 守卫、Python 断言与 extras 语义",
        script="tools/check_shell_scripts.py",
    ),
    Gate(
        name="check_network_boundaries",
        args=("tools/check_network_boundaries.py",),
        sets=_STATIC,
        description="网络访问边界（出网只能经由受控通道）",
        script="tools/check_network_boundaries.py",
        declared_ci_flags=("--source-root",),
        ci_note="CI 的发布线用 --source-root src/omnicrawler 指向源码根；本地自证用默认根。",
    ),
    Gate(
        name="check_release_integrity",
        args=("tools/check_release_integrity.py",),
        sets=_STATIC,
        description="发布完整性（无产物时可校验仓库侧约束）",
        script="tools/check_release_integrity.py",
        declared_ci_flags=(
            "--wheel-dir",
            "--source-zip-dir",
            "--require-released-changelog",
            "--portable-zip-dir",
            "--portable-deep",
            "--portable-platform",
            "--portable-tar",
        ),
        ci_note=(
            "CI 会在有产物时追加 --wheel-dir / --source-zip-dir / --require-released-changelog，"
            "归档线再追加 --portable-*；本地自证不传这些旗标（无产物），差异即此处声明。"
        ),
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
        name="check_dependency_consistency",
        args=("tools/check_dependency_consistency.py",),
        sets=_STATIC,
        description="依赖声明一致性（OpenCV 单变体、无死依赖、版本上下界齐备）",
        script="tools/check_dependency_consistency.py",
    ),
    Gate(
        name="check_build_profiles",
        args=("tools/check_build_profiles.py",),
        sets=_STATIC,
        description="安装/构建档位与 constraints/profiles.toml 逐字符一致（档位唯一真源）",
        script="tools/check_build_profiles.py",
    ),
    Gate(
        name="check_extras_expansion",
        args=("tools/check_extras_expansion.py",),
        sets=_STATIC,
        description="聚合 extra（full / full-macos）必须逐条等于功能 extra 的展开序（顺序唯一真源）",
        script="tools/check_extras_expansion.py",
    ),
    Gate(
        name="check_silent_exception_handlers",
        args=("tools/check_silent_exception_handlers.py",),
        sets=_STATIC,
        description="宽类型静默吞异常必须带 '# noqa: BLE001'（吞可以，静默不行）",
        script="tools/check_silent_exception_handlers.py",
    ),
    Gate(
        name="check_gitignore_hygiene",
        args=("tools/check_gitignore_hygiene.py",),
        sets=_STATIC,
        description="公开 .gitignore 的注释不含绝对路径/本机用户名/悬空文档引用（规则本身不得被忽略）",
        script="tools/check_gitignore_hygiene.py",
    ),
    Gate(
        name="check_doc_numbers",
        args=("tools/check_doc_numbers.py",),
        sets=_STATIC,
        description="现状文档的派生数字由工具算出（禁止手抄门禁数/测试数/i18n 数；生成块逐字比对）",
        script="tools/check_doc_numbers.py",
        ci_note=(
            "扫根目录 *.md、docs/*.md、constraints/*.md；历史事实陈述（CHANGELOG.md、"
            "*_REPORT.md、*_LEDGER.md、docs/archive/、docs/releases/、文件名带日期）不扫——"
            "写当时的数字是对的。维护者改完文档跑 `--update` 让生成块重算。"
        ),
    ),
    Gate(
        name="check_subprocess_encoding",
        args=("tools/check_subprocess_encoding.py",),
        sets=_STATIC,
        description=(
            "按 UTF-8 解子进程输出的调用必须给子进程 UTF-8 环境（防 Windows 控制台代码页造成的假红）"
        ),
        script="tools/check_subprocess_encoding.py",
        ci_note=(
            "AST 判据，只认同 subprocess 发起调用的 encoding= 关键字（不会误伤 "
            "Path.read_text(encoding=...)）。git/bash 之类按 UTF-8 写的外部程序、以及被探测方"
            "自己 reconfigure 的情形，在脚本的 ALLOWED 里逐条写理由放行。"
        ),
    ),
    Gate(
        name="check_gui_test_layout",
        args=("tools/check_gui_test_layout.py",),
        sets=_STATIC,
        description="GUI 测试双目录归属规则（无双名文件、单元层不反向依赖集成层）",
        script="tools/check_gui_test_layout.py",
    ),
    Gate(
        name="check_gui_state_threading",
        args=("tools/check_gui_state_threading.py",),
        sets=_STATIC,
        description="UI 层直连 StateStore 点位白名单（只降不升，新文件禁止直连）",
        script="tools/check_gui_state_threading.py",
    ),
    Gate(
        name="check_main_window_size",
        args=("tools/check_main_window_size.py",),
        sets=_STATIC,
        description="MainWindow 组合根规模 ratchet（行数/方法数/最长方法只降不升）",
        script="tools/check_main_window_size.py",
    ),
    Gate(
        name="check_minimal_install",
        args=("tools/check_minimal_install.py",),
        sets=_STATIC,
        description="核心能力不依赖可选 extras",
        script="tools/check_minimal_install.py",
        ci_note="CI 在**最小安装**环境里跑（只 pip install -e .），证据更强；本地通常在完整 dev 环境里跑。",
    ),
    Gate(
        name="check_market_content",
        args=("tools/check_market_content.py",),
        sets=_STATIC,
        description="市场内容质量（审查清单里可机器判定的部分 → gates_evidence / review_depth）",
        script="tools/check_market_content.py",
        requires_file="../OmniCrawler-market",
        ci_note="需要市场 checkout（CI 由 checkout_market.py 拷到同级目录）；缺失时显式跳过。",
    ),
    Gate(
        name="check_agent_surface",
        args=("tools/check_agent_surface.py", "--check"),
        sets=_STATIC,
        description="agent 面：CLI 契约（命令/参数/退出码/stdout 形态）完整且双向自洽",
        script="tools/check_agent_surface.py",
        ci_note=(
            "防两类沉默失效：① 解析器有命令而注册表没有处理函数（解析器里有 ≠ 能执行）；"
            "② 命令的 stdout 形态既不可验证也未声明（agent 只能靠猜）。命令数为 0 亦判红。"
        ),
    ),
    # ---------- tests ----------
    Gate(
        name="pytest",
        args=("-m", "pytest", "-q"),
        sets=frozenset({"tests"}),
        description="测试套件（CI 以 coverage run 形式执行同一入口）",
    ),
    Gate(
        name="check_visual_regression",
        args=("tools/check_visual_regression.py",),
        sets=frozenset({"tests"}),
        description="同一渲染环境下比较已审查提交与当前界面，空集合或跳过判失败",
        script="tools/check_visual_regression.py",
        needs_args=("output",),
        ci_note="需提供新的证据目录；本地直接执行 --output <新目录>，CI 的 GUI 作业提供目录并上传截图。",
    ),
    # ---------- coverage ----------
    Gate(
        name="check_coverage_gates",
        args=("tools/check_coverage_gates.py", "coverage.json"),
        sets=frozenset({"coverage"}),
        description="按包 / 关键模块的覆盖率下限与分组门禁",
        script="tools/check_coverage_gates.py",
        requires_file="coverage.json",
        declared_ci_flags=("--profile",),
        ci_note="CI 的两个覆盖率作业分别用 --profile core / --profile browser；本地不传则按环境自动选档。",
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
        name="check_sbom_lock",
        args=("tools/check_sbom_lock.py",),
        sets=frozenset({"release"}),
        description="发布环境实际安装版本与 uv.lock 逐包一致",
        script="tools/check_sbom_lock.py",
        needs_args=("sbom", "lock"),
        ci_note="需先在目标平台按 edition 构建并生成 SBOM。",
    ),
    Gate(
        name="check_artifact_budget",
        args=("tools/check_artifact_budget.py",),
        sets=frozenset({"release"}),
        description="分发产物体积预算",
        script="tools/check_artifact_budget.py",
        needs_args=("platform", "directory"),
        declared_ci_flags=("--output",),
        ci_note="需先构建对应平台产物；CI 另传 --output 落报告文件。",
    ),
    Gate(
        name="check_portable_doc_manifest",
        args=("tools/check_portable_doc_manifest.py", "--release-root", "<release-root>"),
        sets=frozenset({"release"}),
        description="便携包文档清单（历史目录不得随包分发、必备根文件齐备）",
        script="tools/check_portable_doc_manifest.py",
        needs_args=("release-root",),
        ci_note=(
            "构建期由三个 build_* 脚本对暂存目录本地执行；归档级复核在 portable_archive_smoke "
            "解压后的 release_root 上由 tools/portable_archive_smoke.py 内部调用；"
            "本门禁独立执行需 --release-root 指向解压后的便携包根目录。"
        ),
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
