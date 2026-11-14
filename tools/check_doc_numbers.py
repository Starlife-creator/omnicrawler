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

* 扫：根目录 `*.md`、`docs/*.md`、`constraints/*.md`。
* 不扫（历史事实陈述，改它们等于改历史）：`CHANGELOG.md`、`*_REPORT.md`、
  `*_LEDGER.md`、`docs/archive/`、`docs/releases/`、文件名带 `YYYY-MM-DD` 的文档。
* 守的是「文档数字 == 工具输出」，**不守数字背后的判断对不对**（例如覆盖率够不够）。

用法::

    python tools/check_doc_numbers.py            # 校验，违规即退出 1
    python tools/check_doc_numbers.py --update   # 按 provider 重写生成块
    python tools/check_doc_numbers.py --json     # 机器可读结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 直接以脚本方式运行时（`python tools/check_doc_numbers.py`），仓库根不在 sys.path 上，
# 无法 `import tools.i18n_stats`；显式补上，使 provider 与测试用同一份真源。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: 参与扫描的文档形态：现状声明，不是历史记录。
DOC_GLOBS = ("*.md", "docs/*.md", "constraints/*.md")

#: 文档之外、但同样**向读者声明现状**的源文件。
#: `tools/gate_registry.py` 的模块 docstring 曾写死「现有 19 个 `tools/check_*.py`」，
#: 而磁盘上早已是 28 个——它是最早一处手抄门禁数量的地方，不能只扫 `.md` 就放过它。
EXTRA_SOURCES = ("tools/gate_registry.py",)

#: 历史文档：带日期的事实陈述，写当时的数字是对的。
HISTORY_DOCS = frozenset({"CHANGELOG.md"})
HISTORY_SUFFIXES = ("_REPORT.md", "_LEDGER.md")
HISTORY_DIRS = ("docs/archive/", "docs/releases/")

#: 文件名里带日期 ⇒ 该文档描述的是那一天的状态。
_DATE_IN_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")

#: 不进入扫描的目录（历史快照 / 依赖 / 缓存）。
_SKIP_PARTS = frozenset(
    {".git", ".venv", ".tmp", ".audit-tmp", ".agents", "node_modules", "__pycache__", ".runtime"}
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
    """README / 用户指南里的 i18n 现状——数字来自 ``tools/i18n_stats.py`` 的实测。"""
    from tools.i18n_stats import measure

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
    # 「28 项 `tools/check_*.py`」/「10 项 tools/check_」
    ("gate-count-phrase", re.compile(r"(\d+)\s*(?:项|个|条)\s*`?tools/check_")),
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
    return bool(_DATE_IN_NAME.search(Path(relative).name))


def scan_targets(root: Path = PROJECT_ROOT) -> tuple[list[Path], list[Path]]:
    """返回 （需要扫描的现状文档, 被排除的历史文档）。"""
    candidates: set[Path] = set()
    for pattern in DOC_GLOBS:
        candidates.update(root.glob(pattern))
    for relative in EXTRA_SOURCES:
        extra = root / relative
        if extra.is_file():
            candidates.add(extra)
    scanned: list[Path] = []
    excluded: list[Path] = []
    for path in sorted(candidates):
        if not path.is_file():
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
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
                        "  修正：python tools/check_doc_numbers.py --update",
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


def _scan_handcopied(
    relative: str, lines: list[str], inside: set[int], findings: list[Finding]
) -> None:
    for number, line in enumerate(lines, start=1):
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
