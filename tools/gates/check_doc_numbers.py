#!/usr/bin/env python3
"""现状文档里的派生数字必须由工具算出——手抄的数字一定会漂。

## 为什么需要这条门禁

`docs/TEST_REPORT.md`、`CHANGELOG.md` 之类是**带日期的事实陈述**，写「当时 23/0/0」
是历史记录；但 `CONTRIBUTING.md`、`README.md`、`constraints/README.md` 是**现状声明**，
里面的数字每漂一次就要靠人记得改，而人一定会漏。实证：

* `CONTRIBUTING.md` 曾写「`pytest tests/gui` = 158 passed, 1 skipped」，实测可收集项数
  早已是另一个量级——而且「passed」与「collected」根本不是同一口径；
* `constraints/README.md` 曾写「local plugin regression (337 passed, 8 skipped)」，
  同样与实测不符；
* `README.md` 一边手抄 i18n 覆盖率四个数字，一边在下一行声明「README 不再手抄数字」。

旧的判据（只认「N 项 `tools/check_`」一种句式、只扫两个文件）覆盖不到这些形态，
于是它们长期共存且无人发现。

## 两道判据

1. **禁止句式**：现状文档里不得出现「派生数字」的手抄形态——门禁数量、测试通过数、
   用例条数、i18n 条目数等。一律改写成**命令指针**（写命令，不写数字）。
   「选中 0 个用例」这类**退化规则表述**不算实测值，数值为 0 时放行。
2. **生成块逐字比对**：确实必须露出的数字放进
   `<!-- BEGIN AUTOGEN:<key> -->` … `<!-- END AUTOGEN:<key> -->`，
   块内容由本脚本按下表的 provider **当场算出**并写回（`--update`）。
   校验时比的是**计算值**，不是仓库里存的副本——否则这里就是第三份手抄源。

## 扫描面与边界

* 扫：**文件系统递归的全部 `.md`**（任意深度，含还没 `git add` 的新文件），外加 `tools/gate_registry.py` 的模块 docstring。
* 不扫（历史事实陈述，改它们等于改历史）：`CHANGELOG.md`、`*_REPORT.md`、
  `*_LEDGER.md`、`docs/archive/`、`docs/releases/`、**路径任意一级**带 `YYYY-MM-DD` 的文档
  （日期在目录上与在文件名上是同一个语义：`user-test-runs/2026-11-01/x.md` 属于那一天）。
* 不扫（副本 / 生成产物 / 依赖，逐条列在 `_SKIP_PARTS` 与 `_SKIP_PREFIXES` 并写明理由）：
  `.git`、`.venv`、`node_modules`、`__pycache__`、`.tmp`、`.audit-tmp`、`.agents`、
  `.test-tmp`、`pytest-of-Lenovo`、`build_cache`、`e2e-artifacts`、`.runtime`、
  `user-test-runs/dependency-depth-implementation-20261007/`（该批运行产物）。
* HTML 注释 `<!-- ... -->` 里的内容不参与手抄扫描：那里常放**格式示例**（
  `PULL_REQUEST_TEMPLATE.md` 的「例如：汇总：14 通过 / 0 失败 / 0 跳过」），
  不是现状声明；注释外的正文照旧逐字扫描，屏蔽不会顺延到下一行。
* 守的是「文档数字 == 工具输出」，**不守数字背后的判断对不对**（例如覆盖率够不够）。

用法::

    python tools/gates/check_doc_numbers.py            # 校验，违规即退出 1
    python tools/gates/check_doc_numbers.py --update   # 按 provider 重写生成块
    python tools/gates/check_doc_numbers.py --json     # 机器可读结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 直接以脚本方式运行时（`python tools/gates/check_doc_numbers.py`），仓库根不在 sys.path 上，
# 无法 `import tools.i18n.i18n_stats`；显式补上，使 provider 与测试用同一份真源。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: 参与扫描的文档形态：现状声明，不是历史记录。
#:
#: **递归枚举全部 `.md`，不再用固定几层 glob。** 过去是
#: ``("*.md", "docs/*.md", "constraints/*.md")`` 三条硬编码模式，只覆盖根目录、
#: `docs/` 一层和 `constraints/` 一层；`docs/adr/`、`src/omnicrawler/gui/docs/`、
#: `.github/`、`plugins/`、`packaging/` 这些**会新增文档**的位置一层都盖不到。
#: 而漏扫是**静默**的：新文档里手抄了派生数字，门禁照样报「通过」。
#: 2026-11-14 实测：往 `docs/adr/` 塞一份写「门禁共 31 项」的现状文档，旧扫描面命中 0 条；
#: 同样的内容放在 `docs/` 下命中 1 条——缺口是真的，不是理论上的。
#:
#: 用文件系统递归而不是 ``git ls-files``：后者只列**已跟踪**文件，新增但还没
#: `git add` 的文档照样漏，等于把同一个缺口换个形式放回来；打包副本与 worktree 下
#: git 状态也不可靠。递归枚举 + 显式排除集，覆盖面才是可证伪的。
DOC_SUFFIX = ".md"

#: 文档之外、但同样**向读者声明现状**的源文件。
#: `tools/gate_registry.py` 的模块 docstring 曾写死「现有 19 个 `tools/check_*.py`」，
#: 而磁盘上早已是 28 个——它是最早一处手抄门禁数量的地方，不能只扫 `.md` 就放过它。
EXTRA_SOURCES = ("tools/gate_registry.py",)

#: 历史文档：带日期的事实陈述，写当时的数字是对的。
HISTORY_DOCS = frozenset({"CHANGELOG.md"})
HISTORY_SUFFIXES = ("_REPORT.md", "_LEDGER.md")
HISTORY_DIRS = ("docs/archive/", "docs/releases/")

#: 路径里（**任意一级目录**）带日期 ⇒ 该文档描述的是那一天的状态。
#:
#: 过去只认**文件名**里的日期，于是 `user-test-runs/2026-11-01/PREACCEPTANCE_CLOSURE.md`
#: 因为日期在**目录**上而被当成现状文档，里面「495 passed / 1 skipped」这类当时的实测
#: 数字全成了违规——那是分类错，不是内容漂。目录日期与文件名日期是同一个语义，
#: 判据必须一致。
_DATE_IN_PATH = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 不进入扫描的目录（副本 / 生成产物 / 依赖 / 测试遗留）。
#:
#: 每条都写理由，不许无声跳过：排除集本身也是判据的一部分。
_SKIP_PARTS = frozenset(
    {
        ".git",  # 版本库内部
        ".venv",  # 依赖环境
        "node_modules",  # 依赖环境
        "__pycache__",  # 字节码缓存
        ".tmp",  # 本仓库约定的临时脚本目录
        ".audit-tmp",  # 审计快照副本，内容与真仓重复
        ".agents",  # 子代理 worktree 副本（独立 git 树，另一份真仓）
        ".test-tmp",  # pytest 的临时根（本仓把 TEMP 指到这里）
        "pytest-of-Lenovo",  # 未重定向时 pytest 落在系统 TEMP 的遗留
        "build_cache",  # 构建产物（含第三方模型的 README）
        "e2e-artifacts",  # E2E 运行产物
        ".runtime",  # 运行时缓存
    }
)

#: 具体路径前缀排除（目录级 :data:`_SKIP_PARTS` 表达不了的，逐条给理由）。
_SKIP_PREFIXES: tuple[tuple[str, str], ...] = (
    (
        "user-test-runs/dependency-depth-implementation-20261007/cache",
        "pytest 自己的 cache 目录副本，里面的 README.md 是 pytest 模板不是本仓文档",
    ),
    (
        "user-test-runs/dependency-depth-implementation-20261007",
        "该批次的运行产物目录（含 cache/v/*），不是现状声明",
    ),
)

AUTOGEN_BEGIN = "BEGIN AUTOGEN:"
AUTOGEN_END = "END AUTOGEN:"
_BLOCK_BEGIN_RE = re.compile(r"^\s*<!--\s*BEGIN AUTOGEN:([a-z0-9][a-z0-9-]*)\s*-->\s*$")
_BLOCK_END_RE = re.compile(r"^\s*<!--\s*END AUTOGEN:([a-z0-9][a-z0-9-]*)\s*-->\s*$")


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def describe(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


# ---------------------------------------------------------------------------
# provider：块内容的唯一真源。每个 key 一个函数，返回值**当场算出**。
# ---------------------------------------------------------------------------


def _render_i18n_summary() -> list[str]:
    """README / 用户指南里的 i18n 现状——数字来自 ``tools/i18n/i18n_stats.py`` 的实测。"""
    from tools.i18n.i18n_stats import measure

    stats = measure()
    pot = int(stats["pot_entries"])  # type: ignore[arg-type]
    po = int(stats["po_entries"])  # type: ignore[arg-type]
    translated = int(stats["translated"])  # type: ignore[arg-type]
    coverage = float(stats["coverage"])  # type: ignore[arg-type]
    ratio = round(po / pot * 100, 1) if pot else 0.0
    return [
        f"模板条目 {pot} 条 · 语言包登记 {po} 条 · 非空译文 {translated} 条 · "
        f"覆盖 {coverage}%（按登记口径 {po}/{pot} ≈ {ratio}%）"
    ]


PROVIDERS: dict[str, Callable[[], list[str]]] = {
    "i18n-summary": _render_i18n_summary,
}


# ---------------------------------------------------------------------------
# 手抄派生数字：禁止句式
# ---------------------------------------------------------------------------

#: 每条规则都是「派生数字 + 单位/上下文」的形态；命中即要求改写成命令指针。
#: 数值为 0 的一律放行——「选中 0 个用例必须判红」这类表述是规则，不是实测值。
FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 「28 项 `tools/check_*.py`」/「10 项 tools/gates/check_」
    ("gate-count-phrase", re.compile(r"(\d+)\s*(?:项|个|条)\s*`?tools/(?:gates/)?check_")),
    # 「29 项门禁」/「门禁 29 条」
    ("gate-count-cn", re.compile(r"(\d+)\s*(?:项|个|条)\s*门禁")),
    ("gate-count-cn-rev", re.compile(r"门禁[^\n。；，]{0,6}?(\d+)\s*(?:项|个|条)")),
    # 「158 passed」——测试通过数是环境与批次相关的实测值
    ("test-pass-count", re.compile(r"(?<![\d/.-])(\d+)\s+passed\b")),
    # 「28 通过 / 1 失败」「28 通过 / 0 失败 / 0 跳过」——同上，只是中文写法
    ("test-pass-count-cn", re.compile(r"(?<![\d/.-])(\d+)\s*(?:条|个|项)?\s*(?:通过|失败|跳过)\s*[/／]")),
    # 「7 条用例」
    ("case-count", re.compile(r"(\d+)\s*(?:条|个|项)\s*用例")),
    # i18n 条目数与覆盖率：必须走生成块
    ("i18n-inline", re.compile(r"(?:模板条目|语言包|已译|未译|译文|翻译)[^\n]{0,20}?\d")),
)


def _number(text: str) -> int | None:
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


# ---------------------------------------------------------------------------
# 扫描面
# ---------------------------------------------------------------------------


def is_history_doc(relative: str) -> bool:
    """判断一份文档是否是「历史事实陈述」（不参与现状数字扫描）。"""
    if relative in HISTORY_DOCS:
        return True
    if any(relative.endswith(suffix) for suffix in HISTORY_SUFFIXES):
        return True
    if any(relative.startswith(prefix) for prefix in HISTORY_DIRS):
        return True
    # 日期在**任意一级**路径上——文件名或目录，语义相同。
    return bool(_DATE_IN_PATH.search(relative))


def is_skipped_path(relative: str) -> bool:
    """判断一份文档是否落在副本 / 生成产物 / 缓存目录里（排除集的唯一真源）。"""
    parts = relative.split("/")
    if any(part in _SKIP_PARTS for part in parts[:-1]):
        return True
    return any(relative == prefix or relative.startswith(prefix + "/") for prefix, _ in _SKIP_PREFIXES)


def _iter_documents(root: Path) -> Iterator[Path]:
    """深度优先列出全部 ``.md``，**遇到副本目录整棵剪枝**。

    为什么不用 ``root.rglob("*.md")``：它会把 ``.venv`` / ``.agents`` / ``.audit-tmp``
    这些目录**整棵走一遍**再交给后面过滤，本仓这几个目录里有上千份 ``.md``，白白多花时间。
    剪枝之后耗时与「实际要检查的文档数」成正比，而不是与「磁盘上的文件数」成正比——
    这条门禁在 static 集合里，必须保持秒级。
    """
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue  # 读不到的目录不是致命错误：跳过，但要能看出「什么都没扫到」
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name in _SKIP_PARTS or entry.is_symlink():
                        continue  # 副本/依赖目录整棵跳过；符号链接不跟（防环）
                    stack.append(entry)
                elif entry.suffix.lower() == DOC_SUFFIX and entry.is_file():
                    yield entry
            except OSError:
                continue


def scan_targets(root: Path = PROJECT_ROOT) -> tuple[list[Path], list[Path]]:
    """返回 （需要扫描的现状文档, 被排除的历史文档）。

    扫描面是**文件系统递归**（任意深度），不是固定层数的 glob：新增文档落在任何一层
    都应被看见；漏扫必须是显式的（进 :data:`_SKIP_PARTS` / :data:`_SKIP_PREFIXES`
    并写明理由），不能是「glob 没写到那儿」。
    """
    candidates: set[Path] = set(_iter_documents(root))
    for relative in EXTRA_SOURCES:
        extra = root / relative
        if extra.is_file():
            candidates.add(extra)
    scanned: list[Path] = []
    excluded: list[Path] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if is_skipped_path(relative):
            continue
        (excluded if is_history_doc(relative) else scanned).append(path)
    return scanned, excluded


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _block_content(lines: list[str], start: int, end: int) -> list[str]:
    return [line.strip() for line in lines[start + 1 : end]]


def _validate_blocks(
    relative: str, lines: list[str], findings: list[Finding]
) -> tuple[set[int], int]:
    """校验所有生成块；返回（块内行号集合, 见到的块数）。"""
    inside: set[int] = set()
    consumed_ends: set[int] = set()
    blocks = 0
    index = 0
    while index < len(lines):
        begin = _BLOCK_BEGIN_RE.match(lines[index])
        if not begin:
            index += 1
            continue
        key = begin.group(1)
        end_index: int | None = None
        for probe in range(index + 1, len(lines)):
            end = _BLOCK_END_RE.match(lines[probe])
            if end:
                if end.group(1) != key:
                    findings.append(
                        Finding(
                            relative,
                            probe + 1,
                            "mismatched-block-end",
                            f"结束标记写的是 {end.group(1)!r}，但开始标记是 {key!r}",
                        )
                    )
                end_index = probe
                consumed_ends.add(probe)
                break
        if end_index is None:
            findings.append(
                Finding(
                    relative,
                    index + 1,
                    "unbalanced-block",
                    f"生成块 {key!r} 没有结束标记（删掉标记不等于去掉约束）",
                )
            )
            index += 1
            continue
        blocks += 1
        inside.update(range(index, end_index + 1))
        provider = PROVIDERS.get(key)
        if provider is None:
            findings.append(
                Finding(
                    relative,
                    index + 1,
                    "unknown-block",
                    f"生成块 {key!r} 没有对应的 provider，无法校验也无法重写",
                )
            )
        else:
            actual = _block_content(lines, index, end_index)
            expected = [line.strip() for line in provider()]
            if actual != expected:
                findings.append(
                    Finding(
                        relative,
                        index + 1,
                        "stale-block",
                        f"生成块 {key!r} 与工具当场算出的值不一致：\n"
                        f"  文档里：{actual}\n  算出来：{expected}\n"
                        "  修正：python tools/gates/check_doc_numbers.py --update",
                    )
                )
        index = end_index + 1
    # 只剩结束标记的残块：删掉开始标记也让约束失效，必须判红。
    for number, line in enumerate(lines):
        if number in consumed_ends:
            continue
        orphan = _BLOCK_END_RE.match(line)
        if orphan:
            findings.append(
                Finding(
                    relative,
                    number + 1,
                    "orphan-block-end",
                    f"结束标记 {orphan.group(1)!r} 没有对应的开始标记——"
                    "去掉标记不会让数字变成真值，只会让校验失效",
                )
            )
    return inside, blocks


def mask_html_comments(lines: list[str]) -> list[str]:
    """把 HTML 注释 ``<!-- ... -->`` 的内容换成空格（保留行数与列位置）。

    为什么需要：注释经常用来**给出格式示例**，而不是声明现状。
    `.github/PULL_REQUEST_TEMPLATE.md` 里就有

        <!-- 粘贴实际输出（汇总行即可，例如：汇总：14 通过 / 0 失败 / 0 跳过，共 25.0s） -->

    那句「14 通过 / 0 失败 / 0 跳过」是**占位格式**，不是本仓现在的数字——
    按手抄数字判红属于误伤。生成块的标记本身也在注释里，但那部分由
    :func:`_validate_blocks` 单独处理，不走本函数的扫描路径。

    只屏蔽内容、不改行长：用空格替换可以在保持列号的前提下让正则失效，
    报出来的行号仍然是原文行号。
    """
    masked: list[str] = []
    inside = False
    for line in lines:
        chars = list(line)
        index = 0
        while index <= len(chars):
            if not inside:
                start = line.find("<!--", index)
                if start < 0:
                    break
                stop = line.find("-->", start + 4)
                if stop < 0:
                    for pos in range(start, len(chars)):
                        chars[pos] = " "
                    inside = True
                    break
                for pos in range(start, stop + 3):
                    chars[pos] = " "
                index = stop + 3
            else:
                stop = line.find("-->", index)
                if stop < 0:
                    for pos in range(index, len(chars)):
                        chars[pos] = " "
                    break
                for pos in range(0, stop + 3):
                    chars[pos] = " "
                inside = False
                index = stop + 3
        masked.append("".join(chars))
    return masked


def _scan_handcopied(
    relative: str, lines: list[str], inside: set[int], findings: list[Finding]
) -> None:
    for number, line in enumerate(mask_html_comments(lines), start=1):
        if number - 1 in inside:
            continue  # 生成块内容由 provider 保证，不参与禁止句式扫描
        for rule, pattern in FORBIDDEN:
            for match in pattern.finditer(line):
                if _number(match.group(0)) == 0:
                    continue  # 「选中 0 个用例」是规则表述，不是实测值
                findings.append(
                    Finding(
                        relative,
                        number,
                        rule,
                        f"手抄了派生数字 {match.group(0)!r}——改用命令指针，"
                        f"或放进 {AUTOGEN_BEGIN}<key> --> 生成块由工具重写",
                    )
                )


def check_text(relative: str, text: str) -> list[Finding]:
    """对一段文本跑「禁止手抄派生数字」判据（不含生成块语义）。

    单独暴露是为了让反向断言能直接喂人工样例：正则若写错，样例就不该命中。
    """
    findings: list[Finding] = []
    _scan_handcopied(relative, text.splitlines(), set(), findings)
    return findings


def check(root: Path = PROJECT_ROOT) -> list[Finding]:
    """校验现状文档：禁止句式 + 生成块逐字比对。"""
    scanned, _ = scan_targets(root)
    findings: list[Finding] = []
    if not scanned:
        return [
            Finding(
                "<scan>",
                0,
                "empty-selection",
                "没有扫到任何现状文档——扫描面或 glob 已失效，空集合不是通过",
            )
        ]
    total_blocks = 0
    for path in scanned:
        relative = path.relative_to(root).as_posix()
        lines = _read_lines(path)
        inside, blocks = _validate_blocks(relative, lines, findings)
        total_blocks += blocks
        _scan_handcopied(relative, lines, inside, findings)
    if total_blocks == 0:
        findings.append(
            Finding(
                "<scan>",
                0,
                "no-blocks",
                "全仓现状文档里一个生成块都没有——生成块判据已成空转，"
                "要么被误删，要么文档已改回手抄",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 重写生成块（维护者入口；CI 只校验）
# ---------------------------------------------------------------------------


def _eol_of(raw: bytes) -> str:
    return "\r\n" if b"\r\n" in raw else "\n"


def update(root: Path = PROJECT_ROOT) -> list[str]:
    """按 provider 重写所有生成块，返回被改写的文件列表。"""
    scanned, _ = scan_targets(root)
    rewritten: list[str] = []
    for path in scanned:
        raw = path.read_bytes()
        eol = _eol_of(raw)
        lines = raw.decode("utf-8").splitlines()
        changed = False
        index = 0
        while index < len(lines):
            begin = _BLOCK_BEGIN_RE.match(lines[index])
            if not begin:
                index += 1
                continue
            key = begin.group(1)
            provider = PROVIDERS.get(key)
            end_index = next(
                (probe for probe in range(index + 1, len(lines)) if _BLOCK_END_RE.match(lines[probe])),
                None,
            )
            if provider is None or end_index is None:
                index += 1
                continue
            expected = provider()
            if _block_content(lines, index, end_index) != [line.strip() for line in expected]:
                lines[index + 1 : end_index] = expected
                end_index = index + 1 + len(expected)
                changed = True
            index = end_index + 1
        if changed:
            path.write_bytes((eol.join(lines) + eol).encode("utf-8"))
            rewritten.append(path.relative_to(root).as_posix())
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="现状文档的数字必须由工具算出：禁止手抄派生数字，生成块逐字比对。"
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--update",
        action="store_true",
        help="按 provider 重写生成块（维护者入口），不做校验",
    )
    parser.add_argument("--json", action="store_true", help="机器可读结果")
    args = parser.parse_args(argv)

    if args.update:
        rewritten = update(args.root)
        if args.json:
            print(json.dumps({"ok": True, "rewritten": rewritten}, ensure_ascii=True, indent=2))
        else:
            print(
                "已重写生成块：" + (", ".join(rewritten) if rewritten else "无变化（已是最新）")
            )
        return 0

    findings = check(args.root)
    if args.json:
        payload = {"ok": not findings, "findings": [f.describe() for f in findings]}
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    elif findings:
        print("文档数字一致性检查失败：", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.describe()}", file=sys.stderr)
    else:
        scanned, excluded = scan_targets(args.root)
        print(
            f"文档数字一致性检查通过（扫描 {len(scanned)} 份现状文档，"
            f"跳过 {len(excluded)} 份历史文档）。"
        )
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
