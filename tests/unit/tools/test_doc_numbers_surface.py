"""扫描面本身要可证伪：递归覆盖、排除集有理由、历史分类一致、注释不误伤。

背景（2026-11-14 实测）：旧扫描面是三条硬编码 glob
（``("*.md", "docs/*.md", "constraints/*.md")``），只覆盖根目录、``docs/`` 一层、
``constraints/`` 一层。拿 git 已跟踪的 190 份 ``.md`` 比对，现状文档 68 份里有 16 份
**完全在扫描面之外**：``docs/adr/`` 三份、``src/omnicrawler/gui/docs/`` 三份、
``.github/`` 三份，以及 ``plugins/``、``e2e/``、``packaging/``、``assets/``、
``artifacts/``、``user-test-runs/`` 等。往 ``docs/adr/`` 塞一份写「门禁共 31 项」的
现状文档，``check()`` 命中 **0** 条；同样的内容放在 ``docs/`` 下命中 1 条。

漏扫是**静默**的——门禁照样报「通过」。所以扫描面本身必须被断言钉住：

1. 递归覆盖：任意深度的新增文档都进扫描集（含还没 ``git add`` 的）；
2. 不得收窄：旧的 52 份仍在扫描集里（照 ``fec3a4b6`` 那条「扫描面不得收窄」守卫）；
3. 排除集显式：每条排除都有理由，且不许把 ``.github`` 之类正常目录吞掉；
4. 历史分类一致：日期在**目录**上也算历史（``user-test-runs/2026-11-01/``）；
5. HTML 注释里的格式示例不算现状声明，但不得因此漏掉同一行/下一行的真违规。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_doc_numbers as gate

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 一份「现状文档 + 手抄派生数字」的最小违规样例。
_VIOLATION = "# 决策记录\n\n本仓静态门禁共 31 项，`tools/check_` 前缀的脚本都在 tools/ 下。\n"


def _write(root: Path, relative: str, text: str) -> Path:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def _scanned_relatives(root: Path) -> set[str]:
    scanned, _ = gate.scan_targets(root)
    return {path.relative_to(root).as_posix() for path in scanned}


def _rules(findings: list[gate.Finding]) -> set[str]:
    return {finding.rule for finding in findings}


# ---------------------------------------------------------------------------
# 1. 递归覆盖：任意深度的新增文档都必须被看见
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "docs/adr/0001-new-decision.md",  # 旧扫描面漏掉的真实位置
        "src/omnicrawler/gui/docs/design.md",  # 嵌套在 src 下
        ".github/PULL_REQUEST_TEMPLATE.md",  # 点目录
        "plugins/README.md",
        "a/b/c/d/e/f/deep.md",  # 深度无关
    ],
)
def test_recursive_scan_covers_arbitrary_depth(tmp_path: Path, relative: str) -> None:
    """**反向断言（核心）**：新增文档放在哪一层都必须进扫描集。"""
    _write(tmp_path, relative, _VIOLATION)
    assert relative in _scanned_relatives(tmp_path), f"{relative} 没被扫到——递归覆盖失效"


def test_new_nested_document_forces_red(tmp_path: Path) -> None:
    """缺口的原始复现：``docs/adr/`` 下的违规文档必须让门禁红，而不是静默通过。"""
    _write(tmp_path, "README.md", "# 根文档\n")
    _write(tmp_path, "docs/adr/0001-new.md", _VIOLATION)

    findings = gate.check(tmp_path)

    hits = [finding for finding in findings if "docs/adr/0001-new.md" in finding.path]
    assert hits, f"docs/adr/ 下的手抄数字被静默放行了: {[f.describe() for f in findings]}"
    assert _rules(hits) & {"gate-count-cn-rev", "gate-count-cn", "gate-count-phrase"}


def test_control_new_nested_document_without_violation_is_green(tmp_path: Path) -> None:
    """对照组：同一位置、没有手抄数字的文档不得被判红（否则上面的用例只是「一律红」）。

    根文档里放一个合法的生成块：``check()`` 在「全仓一个生成块都没有」时会判
    ``no-blocks``（那条是防生成块判据空转，与本节无关），这里把它满足掉，
    好让断言只针对「新增文档是否被判红」。
    """
    _write(
        tmp_path,
        "README.md",
        "# 根文档\n\n<!-- BEGIN AUTOGEN:i18n-summary -->\n<!-- END AUTOGEN:i18n-summary -->\n",
    )
    _write(tmp_path, "docs/adr/0001-new.md", "# 决策记录\n\n数字一律走命令指针。\n")

    findings = gate.check(tmp_path)
    assert [f for f in findings if "docs/adr/0001-new.md" in f.path] == [], findings


def test_scan_does_not_depend_on_git(tmp_path: Path) -> None:
    """扫描面不得依赖 ``git ls-files``——新增但还没 ``git add`` 的文件会漏。

    守卫看的是**代码**而不是注释：源码里不得 ``import subprocess``、不得出现
    ``"ls-files"`` 这个字符串字面量。只要有人把它改回 git 方案，这条就红，
    逼他先解释「未 add 的新文档怎么办」。
    """
    source = (_REPO_ROOT / "tools" / "gates" / "check_doc_numbers.py").read_text(encoding="utf-8")
    assert "import subprocess" not in source, "扫描面又开始跑子进程了（git？）：未 add 的新文档会漏扫"
    assert '"ls-files"' not in source and "'ls-files'" not in source, "扫描面又依赖 git ls-files 了"

    # 临时目录不是 git 仓，新增文件照样被扫到——行为层的同一条证据。
    _write(tmp_path, "fresh/unstaged.md", "内容")
    assert "fresh/unstaged.md" in _scanned_relatives(tmp_path)


# ---------------------------------------------------------------------------
# 2. 扫描面不得收窄（照 fec3a4b6 那条守卫）
# ---------------------------------------------------------------------------


def test_scan_surface_did_not_narrow() -> None:
    """旧的 52 份现状文档必须仍在扫描集里 —— 扩面不能以丢面为代价。"""
    scanned = _scanned_relatives(_REPO_ROOT)

    must_keep = {
        "README.md",
        "CONTRIBUTING.md",
        "constraints/README.md",
        "OmniCrawler-用户指南.md",
        "docs/CODING_STANDARDS.md",
        "docs/ARCHITECTURE.md",
        "tools/gate_registry.py",  # EXTRA_SOURCES 里的非 .md
    }
    missing = sorted(must_keep - scanned)
    assert missing == [], f"以下旧扫描面成员不见了：{missing}"

    assert len(scanned) >= 53, f"扫描集只有 {len(scanned)} 份，比改造前（53）还少"


def test_forbidden_patterns_still_catch_the_original_samples() -> None:
    """扩面的同时判据本身不能松——旧句式仍必须被抓。"""
    samples = {
        "gate-count-phrase": "以及 10 项 `tools/check_*.py`（架构、SDK 契约）",
        "gate-count-cn": "静态集合一共 29 项门禁",
        "gate-count-cn-rev": "门禁 29 条都在这里",
        "test-pass-count": "`pytest tests/gui` = 158 passed, 1 skipped",
        "test-pass-count-cn": "静态自证 28 通过 / 1 失败",
        "case-count": "证据：7 条用例覆盖了短值保留",
        "i18n-inline": "模板条目 2239 条中，语言包已译 460 条",
    }
    for expected, text in samples.items():
        assert expected in _rules(gate.check_text("sample.md", text)), f"句式 {expected} 失效"


# ---------------------------------------------------------------------------
# 3. 排除集：显式、有理由、不越界
# ---------------------------------------------------------------------------


def test_every_skip_prefix_has_a_reason() -> None:
    """排除不能是无声的：每条 prefix 都要写清「为什么这份不是现状声明」。"""
    for prefix, reason in gate._SKIP_PREFIXES:
        assert prefix, "排除前缀不能为空"
        assert reason.strip(), f"{prefix} 没有写排除理由"
        assert len(reason) >= 8, f"{prefix} 的理由太短，无法复核：{reason!r}"


def test_skip_parts_do_not_swallow_normal_directories() -> None:
    """排除集不许过度——把 ``.github`` / ``docs`` / ``plugins`` 吞掉就等于又回到窄扫描。"""
    overbroad = {"docs", "plugins", "e2e", "assets", ".github", "packaging", "artifacts"}
    assert not (overbroad & set(gate._SKIP_PARTS)), "排除集把正常文档目录吞了"


def test_skip_parts_are_actual_copy_or_cache_directories() -> None:
    """排除项必须都是副本/缓存/依赖类目录，且每一项都真能命中一个路径。"""
    for part in gate._SKIP_PARTS:
        assert part.strip(), "排除项不能为空"
        assert gate.is_skipped_path(f"{part}/x.md"), f"{part} 这个排除项没命中任何路径"

    # 一级目录名命中；同名文件（不是目录）不得被跳过。
    assert gate.is_skipped_path(".agents/worktrees/w/docs/x.md")
    assert not gate.is_skipped_path("docs/.agents.md"), "文件名恰好叫 .agents.md 不该跳过"


def test_nested_pytest_cache_copy_is_excluded_with_reason() -> None:
    """``user-test-runs/.../cache/README.md`` 是 pytest 的 cache 模板，不是本仓文档。"""
    relative = "user-test-runs/dependency-depth-implementation-20261007/cache/README.md"
    assert gate.is_skipped_path(relative)
    assert relative not in _scanned_relatives(_REPO_ROOT)


# ---------------------------------------------------------------------------
# 4. 历史分类：日期在目录上也一样
# ---------------------------------------------------------------------------


def test_directory_date_makes_a_document_history() -> None:
    """日期在**目录**上同样是「那一天的事实陈述」——旧判据只看文件名，分类错。"""
    assert gate.is_history_doc("user-test-runs/2026-11-01/PREACCEPTANCE_CLOSURE.md")
    assert gate.is_history_doc("user-test-runs/2026-11-01/nested/deep.md")
    # 文件名带日期仍要认（旧行为不能丢）
    assert gate.is_history_doc("docs/2026-10-03-something.md")


def test_non_history_documents_stay_current() -> None:
    """别把「带数字的目录名」误判成日期。"""
    assert not gate.is_history_doc("docs/adr/ADR-005-config-field-audit.md")
    assert not gate.is_history_doc("docs/v2/README.md")
    assert not gate.is_history_doc("plugins/README.md")


# ---------------------------------------------------------------------------
# 5. HTML 注释：格式示例不算现状声明，但不得借机藏违规
# ---------------------------------------------------------------------------


def test_comment_example_is_not_a_violation() -> None:
    """``PULL_REQUEST_TEMPLATE.md`` 的占位格式是示例，不是本仓现状数字（原假阳性）。"""
    text = (
        "```\n"
        "<!-- 粘贴实际输出（汇总行即可，例如：汇总：14 通过 / 0 失败 / 0 跳过，共 25.0s） -->\n"
        "```\n"
    )
    assert gate.check_text("PULL_REQUEST_TEMPLATE.md", text) == []


def test_comment_mask_does_not_hide_prose_after_the_comment() -> None:
    """**反向断言**：注释屏蔽不能变成「注释后面一行都免检」。"""
    text = "<!-- 示例：汇总：14 通过 / 0 失败 / 0 跳过 --> 本仓静态门禁共 31 项\n"
    rules = _rules(gate.check_text("doc.md", text))
    assert "gate-count-cn-rev" in rules, "同一行注释外的正文被漏掉了"


def test_multiline_comment_mask_does_not_hide_later_lines() -> None:
    """跨行注释结束后，后面的正文照旧扫描。"""
    text = (
        "<!-- 说明：\n"
        "    占位格式 14 通过 / 0 失败 / 0 跳过\n"
        "-->\n"
        "本仓静态门禁共 31 项\n"
    )
    rules = _rules(gate.check_text("doc.md", text))
    assert "gate-count-cn-rev" in rules


def test_unterminated_comment_does_not_crash_and_masks_to_end() -> None:
    """没有 ``-->`` 的悬空注释按「到文件末尾都是注释」处理，且不抛异常。"""
    text = "<!-- 没关掉的注释\n模板条目 2239 条\n"
    assert gate.check_text("doc.md", text) == []


def test_comment_masking_preserves_line_numbers() -> None:
    """屏蔽只换内容、不删行——报出来的行号必须还是原文行号。"""
    lines = ["第一行", "<!-- x -->", "本仓静态门禁共 31 项"]
    masked = gate.mask_html_comments(lines)
    assert len(masked) == len(lines)
    assert masked[2] == "本仓静态门禁共 31 项"
