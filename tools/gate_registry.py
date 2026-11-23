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
    # ── syntax 层（秒级）────────────────────────────────────────────
    #
    # 只查改动的 .py：ast.parse + ruff + 单文件 mypy。
    # 工作区干净时**通过**（0 个文件不是失败 —— 那是「没有要查的」）。
    Gate(
        name="fast_check",
        args=("tools/fast_check.py",),
        sets=frozenset({"syntax"}),
        description="改动文件的语法/ruff/单文件 mypy（秒级）",
        script="tools/fast_check.py",
        ci_note=(
            "本地快速反馈层。**不进 CI**：CI 跑完整 ruff + mypy 已覆盖；"
            "这一层的价值是本地「改完立刻查」，不是发布判据。"
        ),
    ),

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
        args=("tools/gates/check_coding_standards.py", "src"),
        sets=_STATIC,
        description="编码规范（命名、行尾无关的结构约束等）",
        script="tools/gates/check_coding_standards.py",
    ),
    Gate(
        name="check_architecture",
        args=("tools/gates/check_architecture.py",),
        sets=_STATIC,
        description="架构原则（分层、导入环、预算）",
        script="tools/gates/check_architecture.py",
    ),
    Gate(
        name="check_sdk_api",
        args=("tools/gates/check_sdk_api.py",),
        sets=_STATIC,
        description="SDK 对外 API 契约",
        script="tools/gates/check_sdk_api.py",
    ),
    Gate(
        name="check_cli_docs",
        args=("tools/gates/check_cli_docs.py",),
        sets=_STATIC,
        description="CLI 命令与文档一致",
        script="tools/gates/check_cli_docs.py",
    ),
    Gate(
        name="check_docs_consistency",
        args=("tools/gates/check_docs_consistency.py",),
        sets=_STATIC,
        description="文档导航、版本标记与归档门页",
        script="tools/gates/check_docs_consistency.py",
    ),
    Gate(
        name="post_bump_steps",
        args=("tools/releasing/post_bump_steps.py", "--check-current"),
        sets=_STATIC,
        description="版本收口：声明「当前版本」的锚点页（releases/archive README）与 pyproject 版本一致",
        script="tools/releasing/post_bump_steps.py",
        ci_note=(
            "CI 与本地都用 `--check-current`（只读、不需要 --old/--new）。"
            "`--check --old X --new Y` 是发布后的人工补步模式，两者判据不同。"
        ),
    ),
    Gate(
        name="check_anchor_integrity",
        args=("tools/gates/check_anchor_integrity.py",),
        sets=_STATIC,
        description="锚段完整性：项目根 优化方案.md 的 §一/§1.1/§1.2/§1.3 与登记摘要一致",
        script="tools/gates/check_anchor_integrity.py",
        ci_note=(
            "锚文件在**项目根**（主仓的上一级），CI checkout 拿不到 ⇒ CI 里显式 SKIP"
            "（仍会校验摘要登记表结构）。它守的是维护者本机：根目录不是 git 仓，"
            "改锚不会有任何 diff，摘要变更就是唯一可审痕迹。"
        ),
    ),
    Gate(
        name="check_line_endings_policy",
        args=("tools/gates/check_line_endings_policy.py",),
        sets=_STATIC,
        description="换行规则与 .gitattributes 一致（文本默认 LF，bat/cmd 检出 CRLF）",
        script="tools/gates/check_line_endings_policy.py",
    ),
    Gate(
        name="check_shell_scripts",
        args=("tools/gates/check_shell_scripts.py",),
        sets=_STATIC,
        description="根目录 shell/command 脚本语法、失败保护、venv 守卫、Python 断言与 extras 语义",
        script="tools/gates/check_shell_scripts.py",
    ),
    Gate(
        name="check_network_boundaries",
        args=("tools/gates/check_network_boundaries.py",),
        sets=_STATIC,
        description="网络访问边界（出网只能经由受控通道）",
        script="tools/gates/check_network_boundaries.py",
        declared_ci_flags=("--source-root",),
        ci_note="CI 的发布线用 --source-root src/omnicrawler 指向源码根；本地自证用默认根。",
    ),
    Gate(
        name="check_release_integrity",
        args=("tools/gates/check_release_integrity.py",),
        sets=_STATIC,
        description="发布完整性（无产物时可校验仓库侧约束）",
        script="tools/gates/check_release_integrity.py",
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
        args=("tools/gates/check_lint_budget.py",),
        sets=_STATIC,
        description="ruff 豁免预算只降不升",
        script="tools/gates/check_lint_budget.py",
    ),
    Gate(
        name="check_gui_conventions",
        args=("tools/gates/check_gui_conventions.py",),
        sets=_STATIC,
        description="GUI 约定（无障碍名 / 内联样式 / 裸色值）",
        script="tools/gates/check_gui_conventions.py",
    ),
    Gate(
        name="check_lockfile_consistency",
        args=("tools/gates/check_lockfile_consistency.py",),
        sets=_STATIC,
        description="依赖锁一致性（pyproject ↔ uv.lock，无需安装 uv）",
        script="tools/gates/check_lockfile_consistency.py",
        ci_note="权威复现由专门的 lockfile CI job 负责（uv lock --check + uv sync --locked）；本门禁在无 uv 环境下也能跑。",
    ),
    Gate(
        name="check_dependency_consistency",
        args=("tools/gates/check_dependency_consistency.py",),
        sets=_STATIC,
        description="依赖声明一致性（OpenCV 单变体、无死依赖、版本上下界齐备）",
        script="tools/gates/check_dependency_consistency.py",
    ),
    Gate(
        name="check_build_profiles",
        args=("tools/gates/check_build_profiles.py",),
        sets=_STATIC,
        description="安装/构建档位与 constraints/profiles.toml 逐字符一致（档位唯一真源）",
        script="tools/gates/check_build_profiles.py",
    ),
    Gate(
        name="check_extras_expansion",
        args=("tools/gates/check_extras_expansion.py",),
        sets=_STATIC,
        description="聚合 extra（full / full-macos）必须逐条等于功能 extra 的展开序（顺序唯一真源）",
        script="tools/gates/check_extras_expansion.py",
    ),
    Gate(
        name="benchmark_quality",
        args=("tools/benchmarks/benchmark_quality.py",),
        sets=_STATIC,
        description="采集质量基准（离线）：完整/准确/来源匹配/无多余无重复，字段自报完整度可信",
        script="tools/benchmarks/benchmark_quality.py",
        ci_note=(
            "全程离线（页面由本地 HTTP 服务提供），因此本地自证与 CI 逐字相同；"
            "不需要浏览器、不联网、不写仓库。"
        ),
    ),
    Gate(
        name="check_silent_exception_handlers",
        args=("tools/gates/check_silent_exception_handlers.py",),
        sets=_STATIC,
        description="宽类型静默吞异常必须带 '# noqa: BLE001'（吞可以，静默不行）",
        script="tools/gates/check_silent_exception_handlers.py",
    ),
    Gate(
        name="check_gitignore_hygiene",
        args=("tools/gates/check_gitignore_hygiene.py",),
        sets=_STATIC,
        description="公开 .gitignore 的注释不含绝对路径/本机用户名/悬空文档引用（规则本身不得被忽略）",
        script="tools/gates/check_gitignore_hygiene.py",
    ),
    Gate(
        name="check_doc_numbers",
        args=("tools/gates/check_doc_numbers.py",),
        sets=_STATIC,
        description="现状文档的派生数字由工具算出（禁止手抄门禁数/测试数/i18n 数；生成块逐字比对）",
        script="tools/gates/check_doc_numbers.py",
        ci_note=(
            "扫描面是文件系统递归的全部 *.md（任意深度、含还没 git add 的新文件），"
            "外加 tools/gate_registry.py 的 docstring；副本/依赖/生成产物目录与历史事实陈述"
            "（CHANGELOG.md、*_REPORT.md、*_LEDGER.md、docs/archive/、docs/releases/、"
            "路径任意一级带日期者）不扫——写当时的数字是对的。维护者改完文档跑 `--update` 让生成块重算。"
        ),
    ),
    Gate(
        name="check_subprocess_encoding",
        args=("tools/gates/check_subprocess_encoding.py",),
        sets=_STATIC,
        description=(
            "按 UTF-8 解子进程输出的调用必须给子进程 UTF-8 环境（防 Windows 控制台代码页造成的假红）"
        ),
        script="tools/gates/check_subprocess_encoding.py",
        ci_note=(
            "AST 判据，只认同 subprocess 发起调用的 encoding= 关键字（不会误伤 "
            "Path.read_text(encoding=...)）。git/bash 之类按 UTF-8 写的外部程序、以及被探测方"
            "自己 reconfigure 的情形，在脚本的 ALLOWED 里逐条写理由放行。"
        ),
    ),
    Gate(
        name="check_gui_test_layout",
        args=("tools/gates/check_gui_test_layout.py",),
        sets=_STATIC,
        description="GUI 测试双目录归属规则（无双名文件、单元层不反向依赖集成层）",
        script="tools/gates/check_gui_test_layout.py",
    ),
    Gate(
        name="check_gui_state_threading",
        args=("tools/gates/check_gui_state_threading.py",),
        sets=_STATIC,
        description="UI 层直连 StateStore 点位白名单（只降不升，新文件禁止直连）",
        script="tools/gates/check_gui_state_threading.py",
    ),
    Gate(
        name="check_main_window_size",
        args=("tools/gates/check_main_window_size.py",),
        sets=_STATIC,
        description="MainWindow 组合根规模 ratchet（行数/方法数/最长方法只降不升）",
        script="tools/gates/check_main_window_size.py",
    ),
    Gate(
        name="check_minimal_install",
        args=("tools/gates/check_minimal_install.py",),
        sets=_STATIC,
        description="核心能力不依赖可选 extras",
        script="tools/gates/check_minimal_install.py",
        ci_note="CI 在**最小安装**环境里跑（只 pip install -e .），证据更强；本地通常在完整 dev 环境里跑。",
    ),
    Gate(
        name="check_market_content",
        args=("tools/gates/check_market_content.py",),
        sets=_STATIC,
        description="市场内容质量（审查清单里可机器判定的部分 → gates_evidence / review_depth）",
        script="tools/gates/check_market_content.py",
        requires_file="../OmniCrawler-market",
        ci_note="需要市场 checkout（CI 由 checkout_market.py 拷到同级目录）；缺失时显式跳过。",
    ),
    Gate(
        name="check_agent_surface",
        args=("tools/gates/check_agent_surface.py", "--check"),
        sets=_STATIC,
        description="agent 面：CLI 契约（命令/参数/退出码/stdout 形态）完整且双向自洽",
        script="tools/gates/check_agent_surface.py",
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
        args=("tools/gates/check_visual_regression.py",),
        sets=frozenset({"tests"}),
        description="同一渲染环境下比较已审查提交与当前界面，空集合或跳过判失败",
        script="tools/gates/check_visual_regression.py",
        needs_args=("output",),
        ci_note="需提供新的证据目录；本地直接执行 --output <新目录>，CI 的 GUI 作业提供目录并上传截图。",
    ),
    # ---------- coverage ----------
    Gate(
        name="check_coverage_gates",
        args=("tools/gates/check_coverage_gates.py", "coverage.json"),
        sets=frozenset({"coverage"}),
        description="按包 / 关键模块的覆盖率下限与分组门禁",
        script="tools/gates/check_coverage_gates.py",
        requires_file="coverage.json",
        declared_ci_flags=("--profile",),
        ci_note="CI 的两个覆盖率作业分别用 --profile core / --profile browser；本地不传则按环境自动选档。",
    ),
    # ---------- install（CI matrix 提供参数） ----------
    Gate(
        name="check_extra_install",
        args=("tools/gates/check_extra_install.py",),
        sets=frozenset({"install"}),
        description="每个 extras profile 可独立安装且导入完整",
        script="tools/gates/check_extra_install.py",
        needs_args=("profile",),
        ci_note="需逐 profile 安装（CI matrix）；本地自证显式跳过并列出原因。",
    ),
    # ---------- release（需要构建产物） ----------
    Gate(
        name="check_guardrails",
        args=("tools/gates/check_guardrails.py",),
        sets=frozenset({"release"}),
        description="发布护栏（SBOM、许可、来源等）",
        script="tools/gates/check_guardrails.py",
        needs_args=("sbom",),
        ci_note="需先构建产物并生成 SBOM。",
    ),
    Gate(
        name="check_sbom_lock",
        args=("tools/gates/check_sbom_lock.py",),
        sets=frozenset({"release"}),
        description="发布环境实际安装版本与 uv.lock 逐包一致",
        script="tools/gates/check_sbom_lock.py",
        needs_args=("sbom", "lock"),
        ci_note="需先在目标平台按 edition 构建并生成 SBOM。",
    ),
    Gate(
        name="check_tool_wiring",
        args=("tools/gates/check_tool_wiring.py",),
        sets=_STATIC,
        description="tools/ 下的可执行脚本都有调用者（防「写了就忘」）",
        script="tools/gates/check_tool_wiring.py",
        ci_note=(
            "零网络、纯文本扫描。判据是「每个可执行脚本至少被一处引用」："
            "门禁清单 / CI workflow / 文档 / 被其它模块 import。"
            "本会话新增两个工具却零引用，就是这条要防的。"
        ),
    ),
    # **跨平台模拟**：本机用别的平台身份跑测试。
    #
    # 这一条替代了「只能等 CI」的历史。2026-11-22 实测：模拟 macos/aarch64
    # 只需 114 秒（3 组合 × services+browser），却复现了 CI 连红 5 轮、
    # 每轮约 40 分钟才暴露的 40+ 条失败。
    #
    # 为什么值得放进 static：`current_platform()` / `current_architecture()`
    # 只管「平台名分支」这一类，而这类**恰好是本项目最常见的跨平台缺陷源**
    # （配方声明、能力槽、装配路径都按平台分支）。
    Gate(
        name="platform_matrix",
        args=("tools/qa/platform_matrix.py",),
        # **独立成 `matrix` 集合**，不挂在 static：
        # 本门禁要真跑测试（约 63 秒），而 static 其余门禁合计才 ~40 秒。
        # 挂在 static 里 ⇒ 每次提交多等一分钟，而 99% 的改动与平台无关
        # ⇒ 我会开始跳过 static ⇒ 纪律失效。
        # 2026-11-22 曾误放进 syntax（0.6s 那层），把它撑到 162s；
        # 2026-11-23 独立成集合，各层职责清晰。
        sets=frozenset({"matrix"}),
        description="本机模拟 linux/macos/windows 三平台跑测试",
        script="tools/qa/platform_matrix.py",
        ci_note=(
            "零网络。只在**本地**跑：CI 本身就有三平台真机，重复模拟是浪费。"
            "它的价值是把「等 CI 45 分钟」变成「本地 2 分钟」。"
        ),
    ),
    # 本地开发工具版本必须与 constraints 逐字一致。
    #
    # 维护者 2026-11-23 的原话：「有时候你代码里升级了，但本地也要同步升级」。
    # 实证就在同一天：constraints pin ruff==0.17.6，我本机是 0.16.4 ——
    # 这会让「本地绿、CI 红」，而一轮 CI 要 40 分钟。
    #
    # 它进 static 而不是 CI 专用：CI 本身就是按 constraints 装的，必然通过；
    # **真正的价值在本地**（`--set static` 时立刻告诉你版本漂了）。
    Gate(
        name="check_local_toolchain_sync",
        args=("tools/gates/check_local_toolchain_sync.py",),
        sets=_STATIC,
        description="本地开发工具版本与 constraints/quality.txt 一致",
        script="tools/gates/check_local_toolchain_sync.py",
        ci_note=(
            "零网络。CI 按 constraints 装，必然通过 —— 价值在本地："
            "防止「本地绿、CI 红」。要求逐字相等，不是「装最新版」。"
        ),
    ),
    Gate(
        name="check_agent_surface_help",
        args=("tools/gates/check_agent_surface_help.py",),
        sets=_STATIC,
        description="CLI 选项 help 覆盖率（基线只许降低）——agent 只能靠 --help 认识命令",
        script="tools/gates/check_agent_surface_help.py",
        ci_note=(
            "零网络。AGENTS.md 写「CLI 即 agent 面」，而 2026-11-23 实测"
            "199 个选项里 71 个 help=None（含 components 这种高频命令）。"
        ),
    ),
    Gate(
        name="check_walk_pruning",
        args=("tools/gates/check_walk_pruning.py",),
        sets=_STATIC,
        description="目录遍历必须在进入前剪枝（rglob 后过滤会白跑 12 万条目）",
        script="tools/gates/check_walk_pruning.py",
        ci_note=(
            "零网络、纯 AST。这个反模式**不报错也不红测试**，只让工具悄悄变慢；"
            "而慢门禁的后果是不愿意跑它，于是退回全量验证（本会话真实发生）。"
        ),
    ),
    Gate(
        name="check_schema_derived_whitelists",
        args=("tools/gates/check_schema_derived_whitelists.py",),
        sets=_STATIC,
        description="配置白名单必须从 schema 派生，不得手抄键名",
        script="tools/gates/check_schema_derived_whitelists.py",
        ci_note=(
            "零网络、纯 AST。手抄白名单的后果：schema 加字段时不同步，"
            "而报错通常不说是哪个属性。2026-11-22 加 kernel_family 时踩过。"
        ),
    ),
    Gate(
        name="check_test_platform_defaults",
        args=("tools/gates/check_test_platform_defaults.py",),
        sets=_STATIC,
        description="测试 helper 默认值不得硬编码本平台的平台/架构",
        script="tools/gates/check_test_platform_defaults.py",
        ci_note=(
            "零网络、纯 AST。这类缺陷**本机永远测不出来**（current_architecture() "
            "由执行环境决定），只会在 CI 的 macOS arm64 腿炸。"
            "2026-11-22 实测：CI 连红 5 轮、合计 40+ 条，根因都是这一个模式。"
        ),
    ),
    Gate(
        name="check_workspace_mkdir",
        args=("tools/gates/check_workspace_mkdir.py",),
        sets=_STATIC,
        description="测试的 .workspace.mkdir 必须带 exist_ok（load_config 已建该目录）",
        script="tools/gates/check_workspace_mkdir.py",
        ci_note=(
            "零网络。这类缺陷**只在特定执行顺序下暴露**，本地难复现、CI 稳定红。"
            "2026-11-22 实测有 4 处踩坑（test_workflow_diagnostics x2、"
            "test_recovery_center、test_operation_enhancements）。"
        ),
    ),
    Gate(
        name="check_doc_impl_drift",
        args=("tools/gates/check_doc_impl_drift.py",),
        sets=_STATIC,
        description="文档说「未做」但代码已实现的项目（防文档滞后）",
        script="tools/gates/check_doc_impl_drift.py",
        ci_note=(
            "零网络。工作区根不可达时跳过（CI 单仓场景）。"
            "本会话发现 5 处「尚未开始/界面未做」实际已完成。"
        ),
    ),
    Gate(
        name="check_extras_exist",
        args=("tools/gates/check_extras_exist.py",),
        sets=_STATIC,
        description="所有 pip install -e 声明的 extra 名都真实存在",
        script="tools/gates/check_extras_exist.py",
        ci_note=(
            "零网络。补 check_build_profiles 的盲区：后者只管 constraints/profiles.toml "
            "声明过的档位，而 CI workflow / e2e 脚本里的 install 行不受管辖。"
            "本会话就因 e2e.yml 与 e2e/run.sh 各写一遍而漏改一处。"
        ),
    ),
    Gate(
        name="check_cross_repo_copies",
        args=("tools/gates/check_cross_repo_copies.py",),
        sets=_STATIC,
        description="市场仓的同名工具与主仓判据等价（防只改一边）",
        script="tools/gates/check_cross_repo_copies.py",
        ci_note=(
            "零网络。市场仓不可达时自动跳过（CI 单仓场景）。"
            "本会话踩过：改了主仓的配方门禁、忘改市场仓副本，CI 才暴露。"
        ),
    ),
    Gate(
        name="check_component_discovery",
        args=("tools/gates/check_component_discovery.py",),
        sets=_STATIC,
        description="组件扫描统一走 component_discovery（反向索引）",
        script="tools/gates/check_component_discovery.py",
        ci_note=(
            "零网络、纯 AST + 行扫描。防止「怎么找组件」再次散落各处："
            "上次改目录结构时六处扫描点没跟着改，报错各不相同。"
        ),
    ),
    Gate(
        name="check_component_recipes",
        args=("tools/gates/check_component_recipes.py",),
        sets=_STATIC,
        description="组件配方声明可推导（平台/架构/ABI/闭包/入口）",
        script="tools/gates/check_component_recipes.py",
        # CI 用 `--root /tmp/oc-market/components` 指向**刚克隆的市场仓**，
        # 而本地自证读 `market/` 快照（离线、零网络）。
        # 两者判据同源、目标不同：本地验快照钉值，CI 验市场仓真实状态。
        declared_ci_flags=("--root",),
        ci_note=(
            "零网络、零下载：读 market/ 快照与 deps.lock.json 做集合运算。"
            "闭包快照由 tools/market/component_deps_lock.py 生成（那步才联网）。"
            "CI 用 --root 指向刚克隆的市场仓（验真实状态而非快照）。"
        ),
    ),
    Gate(
        name="check_artifact_budget",
        args=("tools/gates/check_artifact_budget.py",),
        sets=frozenset({"release"}),
        description="分发产物体积预算",
        script="tools/gates/check_artifact_budget.py",
        needs_args=("platform", "directory"),
        declared_ci_flags=("--output",),
        ci_note="需先构建对应平台产物；CI 另传 --output 落报告文件。",
    ),
    Gate(
        name="check_portable_doc_manifest",
        args=("tools/gates/check_portable_doc_manifest.py", "--release-root", "<release-root>"),
        sets=frozenset({"release"}),
        description="便携包文档清单（历史目录不得随包分发、必备根文件齐备）",
        script="tools/gates/check_portable_doc_manifest.py",
        needs_args=("release-root",),
        ci_note=(
            "构建期由三个 build_* 脚本对暂存目录本地执行；归档级复核在 portable_archive_smoke "
            "解压后的 release_root 上由 tools/portable/portable_archive_smoke.py 内部调用；"
            "本门禁独立执行需 --release-root 指向解压后的便携包根目录。"
        ),
    ),
)

#: 全部集合名（供命令行校验）。
GATE_SETS: tuple[str, ...] = (
    # ── 分层：从快到慢 ──────────────────────────────────────────────
    #
    # `syntax` 是**秒级**的一层：只对改动的 .py 做 ast.parse + ruff + 单文件
    # mypy。它不查架构约束、不查锚完整性、不查跨文件一致性 ——
    # 那些是 `static` 的事。
    #
    # 为什么单独一层：`static` 要 30 秒，而改一行代码等 30 秒的实际后果是
    # 「先写下去、等门禁再说」—— 于是一次改坏的结构要等一整轮才能发现。
    # 本会话我用批量正则改坏结构化代码 4 次，每次都是写盘之后才暴露。
    "syntax",
    "static",
    # `matrix`：跨平台模拟跑测试（约 63 秒）。它验的是「按平台名分支的
    # 逻辑」是否正确，与「提交前静态检查」性质不同 —— 挂在 static 里会让
    # 每次提交都多等一分钟，而 99% 的改动与平台无关。
    # 用法：`--set matrix`（跨平台相关改动后跑），或 `--set static,matrix`。
    "matrix",
    "tests",
    "coverage",
    "install",
    "release",
)


def gates_for(sets: frozenset[str] | set[str] | tuple[str, ...]) -> tuple[Gate, ...]:
    """返回属于任一所给集合的门禁，保持 :data:`GATES` 的顺序。"""
    wanted = set(sets)
    unknown = wanted - set(GATE_SETS)
    if unknown:
        raise ValueError(f"未知门禁集合: {sorted(unknown)}（合法值: {list(GATE_SETS)}）")
    return tuple(gate for gate in GATES if gate.sets & wanted)


#: **只在本地跑的集合**，不参与「与 CI 双向一致」校验。
#:
#: `syntax` 是秒级快速反馈层：CI 已经跑完整 `ruff` + `mypy`，
#: 覆盖了它的范围。它的价值是「本地改完立刻查」，不是发布判据 ——
#: 要求它进 CI 等于让 CI 重复执行一遍已经跑过的东西。
_LOCAL_ONLY_SETS = frozenset({"syntax"})

#: **只在本地跑的门禁**（不进 CI），按名字登记。
#:
#: `platform_matrix` 在本机模拟三平台跑测试（114 秒），用来把
#: 「等 CI 40 分钟」压缩成「本地 2 分钟」。而 CI 本身就有三平台真机，
#: 在那里重复模拟是纯粹的浪费 —— 所以它刻意不进 CI。
_LOCAL_ONLY_GATES = frozenset({"platform_matrix"})


def ci_managed_scripts() -> frozenset[str]:
    """返回参与「与 CI 双向一致」校验的脚本集合。

    排除 `_LOCAL_ONLY_SETS` 里的门禁：它们**刻意不进 CI**。
    不排除会让 `test_every_registered_gate_is_wired_into_ci` 误判 ——
    它原先假定「登记的门禁都该在 CI 跑」，而分层之后这不成立了。
    """
    return frozenset(
        gate.script
        for gate in GATES
        if gate.script
        and not (gate.sets & _LOCAL_ONLY_SETS)
        and gate.name not in _LOCAL_ONLY_GATES
    )
