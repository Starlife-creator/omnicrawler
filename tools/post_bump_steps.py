#!/usr/bin/env python3
"""``bump_version.py`` 之后必须手工补的几步（那个工具明确不做的部分）。

用法::

    python tools/post_bump_steps.py --old 0.13.0 --new 0.13.1
    python tools/post_bump_steps.py --old 0.13.0 --new 0.13.1 --report-draft draft.md
    python tools/post_bump_steps.py --check --old 0.13.0 --new 0.13.1   # 只读：还有哪些没做
    python tools/post_bump_steps.py --check-current                     # 门禁用：当前版本自洽性

为什么存在
----------
``tools/bump_version.py`` 会重命名 ``docs/releases/RELEASE_REPORT_<v>.md``，并按一份**固定清单**
替换文档正文里的版本号。因此下面几件事它不做 —— 而漏掉任何一件都会判红或留下漂移：

1. ``uv.lock`` 的项目版本：工具不碰，但 ``check_lockfile_consistency`` 与 CI 的
   ``uv sync --locked`` 都会真校验（**实测**：不补就判红）。
2. 会**声明当前版本**的门禁页（``docs/releases/README.md``、``docs/archive/README.md``）里的
   版本引用：两者都不在工具的替换清单里，也都不在 ``docs/*.md`` 的元数据扫描范围内
   （**实测**：前者在 0.13.0 那次手工补过，后者一路停在 0.12.0 直到 2026-09-20 才发现）。
3. ``docs/*.md`` 的 ``> 适用版本：`` 头：同理 —— 工具清单写下**之后**新增的文档不会被覆盖
   （``docs/LINUX_INSTALL.md`` 就是这么漏过一次）。本步还会把"既不是旧版也不是新版"的
   头部**报出来**（漂移可见，而不是静默留着）。
4. 可选：把写好的发布报告草稿落成 ``docs/releases/RELEASE_REPORT_<new>.md`` 的**真实内容**
   —— 工具只重命名，正文不会自己变。

幂等：每步已完成时报 ``skip``。``--check`` 只读，若有未完成的步骤则退出码非零。
★ 打印文本一律 **ASCII**（Windows runner 默认 cp1252，非 ASCII 字符串会 UnicodeEncodeError）。
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import NamedTuple

_UV_LOCK = "uv.lock"
#: 会**声明当前版本**、但 bump 工具不碰的门禁页（两处实测都会漂移）。
_VERSION_STATEMENT_PAGES = ("docs/releases/README.md", "docs/archive/README.md")
_REPORT_PATH = "docs/releases/RELEASE_REPORT_{version}.md"
_VERSION_HEADER = "> 适用版本："
_PROJECT_ANCHOR = 'name = "omnicrawler-platform"\n'

#: 每份版本声明页里「当前版本」那句的**唯一锚点**（一条正则、一个捕获组）。
#: 这是 `--check-current` 的判据来源：锚点缺失或解析不出 ⇒ 报错，**不是**静默跳过。
#: 只抓「当前版本」那一句，历史版本（归档页里的 1.x / 2.x）不在锚点内，不会被误判。
_CURRENT_VERSION_ANCHORS: tuple[tuple[str, str], ...] = (
    ("docs/releases/README.md", r"`RELEASE_REPORT_(\d+\.\d+\.\d+)\.md`\s*\|\s*当前版本"),
    ("docs/archive/README.md", r"当前版本线为\s*\**(\d+\.\d+\.\d+)\**"),
)

#: `> 适用版本：` 后的版本号；同时接受裸值与加粗 `**1.0.1**` 两种写法。
#: 曾经的 `(\S+)` 会把 `**1.0.1**` 整体当作版本串 ⇒ 加粗文档被误报为漂移（批次 3 修正）。
_HEADER_VERSION = re.compile(
    r"^" + re.escape(_VERSION_HEADER) + r"\s*\**([0-9]+\.[0-9]+\.[0-9]+)\**", re.MULTILINE
)

_STATUS_DONE = "done"
_STATUS_SKIP = "skip"
_STATUS_ERROR = "error"


class StepResult(NamedTuple):
    """一步的结果：``status`` 决定退出码，``message`` 是给人看的一行。"""

    status: str
    message: str


def _pending(result: StepResult) -> bool:
    return result.status == _STATUS_DONE


def fix_uv_lock(root: Path, old: str, new: str, *, dry_run: bool = False) -> StepResult:
    """``uv.lock`` 的项目版本。缺锚点或版本不认识 ⇒ 明确报错，绝不静默通过。"""
    path = root / _UV_LOCK
    if not path.is_file():
        return StepResult(_STATUS_ERROR, f"ERROR  {_UV_LOCK} not found under {root}")
    text = path.read_text(encoding="utf-8")
    if _PROJECT_ANCHOR not in text:
        return StepResult(_STATUS_ERROR, f"ERROR  {_UV_LOCK}: project anchor not found")
    head, _, tail = text.partition(_PROJECT_ANCHOR)
    if tail.startswith(f'version = "{new}"'):
        return StepResult(_STATUS_SKIP, f"skip   {_UV_LOCK}: project version already {new}")
    if not tail.startswith(f'version = "{old}"'):
        actual = tail.splitlines()[0] if tail.splitlines() else "<empty>"
        return StepResult(
            _STATUS_ERROR,
            f"ERROR  {_UV_LOCK}: project version is neither {old} nor {new} ({actual})",
        )
    if not dry_run:
        patched = tail.replace(f'version = "{old}"', f'version = "{new}"', 1)
        path.write_text(head + _PROJECT_ANCHOR + patched, encoding="utf-8")
    return StepResult(_STATUS_DONE, f"done   {_UV_LOCK}: project version {old} -> {new}")


def fix_version_statements(
    root: Path, old: str, new: str, *, dry_run: bool = False
) -> list[StepResult]:
    """会**声明当前版本**的门禁页（工具不碰它们）。

    除了 ``docs/releases/README.md``，还有 ``docs/archive/README.md`` —— 它也写着
    "当前版本线为 **X**"，但既不在 bump 工具的替换清单里、也不在 ``docs/*.md`` 的
    元数据扫描范围内 ⇒ 实测（2026-09-20）它一路停在 0.12.0，直到有人专门去找。
    """
    results: list[StepResult] = []
    for rel in _VERSION_STATEMENT_PAGES:
        path = root / rel
        if not path.is_file():
            results.append(StepResult(_STATUS_ERROR, f"ERROR  {rel} not found"))
            continue
        text = path.read_text(encoding="utf-8")
        if old == new:
            # 同版本重跑：replace 是空操作，若仍报 done 会让 --check 永远判「未完成」
            # （批次 3 实测：--old 1.0.1 --new 1.0.1 时 2 步 pending 恒红）。判据是
            # 「页面里是否还留着旧版引用」，同版本时没有可比对的新旧之分 ⇒ 只能 skip。
            results.append(
                StepResult(_STATUS_SKIP, f"skip   {rel}: old == new ({new}), nothing to rewrite")
            )
            continue
        hits = text.count(old)
        if hits == 0:
            results.append(StepResult(_STATUS_SKIP, f"skip   {rel}: no {old} reference"))
            continue
        if not dry_run:
            path.write_text(text.replace(old, new), encoding="utf-8")
        results.append(StepResult(_STATUS_DONE, f"done   {rel}: {hits} reference(s) {old} -> {new}"))
    return results


def fix_version_headers(
    root: Path, old: str, new: str, *, dry_run: bool = False
) -> tuple[list[StepResult], list[str]]:
    """``docs/*.md`` 的 ``> 适用版本：`` 头；顺带报出**漂移**（既非旧版也非新版）。

    返回 (每文件一步的结果, 漂移清单)。漂移只报告、不修改：某份文档可能**有意**标注
    更早的适用版本，替它改写属于越权。

    ★ 两个必须的精确性要求（都由用例看住）：
    * **只认行首的元数据行**（``^> 适用版本：``）。导航页等文档会在**正文里**引用这个标记，
      按子串匹配会造出"假漂移"（实测：``docs/README.md`` 被误报）。
    * **跳过 ``docs/README.md``**：它是导航页、不是带元数据的文档 —— 文档门禁本身也是这么
      排除的（``docs/*.md`` 里除 README 外都必须带标记）。

    ★ 版本号按**数值**取，不按原始 token：``**1.0.1**`` 与 ``1.0.1`` 是同一版本
    （批次 3 修正：此前用 ``(\\S+)`` 会把加粗写法整体当作版本串 ⇒ 8 条假漂移）。
    """
    results: list[StepResult] = []
    drift: list[str] = []
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return [StepResult(_STATUS_ERROR, f"ERROR  docs/ not found under {root}")], drift
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        found = _HEADER_VERSION.search(text)
        if found is None:
            continue
        rel = f"docs/{path.name}"
        if found.group(1) == new:
            results.append(StepResult(_STATUS_SKIP, f"skip   {rel}: header already {new}"))
            continue
        if found.group(1) == old:
            if not dry_run:
                path.write_text(
                    text[: found.start()] + f"{_VERSION_HEADER}{new}" + text[found.end() :],
                    encoding="utf-8",
                )
            results.append(StepResult(_STATUS_DONE, f"done   {rel}: header {old} -> {new}"))
            continue
        drift.append(f"{rel}: header says {found.group(1)} (expected {new})")
    return results, drift


def install_report(
    root: Path, draft: Path | None, new: str, *, dry_run: bool = False
) -> StepResult:
    """把发布报告草稿落成真实内容（工具只重命名旧报告）。"""
    if draft is None:
        return StepResult(_STATUS_SKIP, "skip   report content: no --report-draft given")
    if not draft.is_file():
        return StepResult(_STATUS_ERROR, f"ERROR  report draft not found: {draft}")
    target = root / _REPORT_PATH.format(version=new)
    if not target.parent.is_dir():
        return StepResult(_STATUS_ERROR, f"ERROR  {target.parent} not found")
    if target.is_file() and target.read_bytes() == draft.read_bytes():
        return StepResult(_STATUS_SKIP, f"skip   {_REPORT_PATH.format(version=new)}: already matches draft")
    if not dry_run:
        shutil.copyfile(draft, target)
    return StepResult(
        _STATUS_DONE, f"done   {_REPORT_PATH.format(version=new)}: content installed from {draft.name}"
    )


def _project_version(root: Path) -> str:
    """当前版本真值：``pyproject.toml`` 的 ``project.version``。

    只读这一处是**有意**的——``_version.py`` 与 ``uv.lock`` 与它的一致性已分别由
    ``check_docs_consistency`` / ``check_lockfile_consistency`` 断言，这里不重复造判据。
    """
    import tomllib

    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(metadata["project"]["version"])


def check_current(root: Path) -> list[str]:
    """``--check-current`` 的判据：**当前版本**是否已经落到「bump 工具不碰」的那些位置。

    这是本工具能挂进门禁的唯一形态：``--check --old X --new Y`` 需要两个版本号，
    门禁没有「上一个版本」可问；而这一模式只问一件事——「声明当前版本的锚点页，
    说的还是当前版本吗？」。它覆盖的正是实测漂移过两次的那类位置：

    * ``docs/releases/README.md``：``| `RELEASE_REPORT_<v>.md` | 当前版本 <v> …``
    * ``docs/archive/README.md``：``当前版本线为 **<v>**``

    每条锚点都用**一条正则**取值：解析不到（页面改写了句式）⇒ 报错而不是静默跳过，
    因为「锚点消失」和「版本正确」必须能区分开。
    """
    problems: list[str] = []
    try:
        version = _project_version(root)
    except (OSError, KeyError, ValueError) as exc:
        return [f"pyproject.toml: cannot read project.version ({exc})"]

    for rel, pattern in _CURRENT_VERSION_ANCHORS:
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: file not found")
            continue
        match = re.search(pattern, path.read_text(encoding="utf-8"))
        if match is None:
            problems.append(f"{rel}: version anchor not found (page wording changed?)")
        elif match.group(1) != version:
            problems.append(
                f"{rel}: states {match.group(1)} but project version is {version}"
            )
    return problems


def run_check_current(root: Path) -> int:
    """打印 ``--check-current`` 的结果并给出退出码。"""
    problems = check_current(root)
    if problems:
        for problem in problems:
            print(f"ERROR  {problem}")
        print(f"check-current: {len(problems)} problem(s)")
        return 1
    print("check-current: version statement pages match the project version")
    return 0


def run(root: Path, old: str, new: str, draft: Path | None, *, dry_run: bool) -> int:
    results: list[StepResult] = [fix_uv_lock(root, old, new, dry_run=dry_run)]
    results.extend(fix_version_statements(root, old, new, dry_run=dry_run))
    header_results, drift = fix_version_headers(root, old, new, dry_run=dry_run)
    results.extend(header_results)
    results.append(install_report(root, draft, new, dry_run=dry_run))

    for result in results:
        print(result.message)
    for line in drift:
        print(f"WARN   version header drift -- {line}")

    errors = [r for r in results if r.status == _STATUS_ERROR]
    pending = [r for r in results if _pending(r)]
    if dry_run:
        if errors:
            print(f"check: {len(errors)} error(s), {len(pending)} step(s) pending")
            return 1
        if pending:
            print(f"check: {len(pending)} step(s) still pending")
            return 1
        print("check: all post-bump steps are complete")
        return 0
    if errors:
        print(f"post-bump steps finished with {len(errors)} error(s)")
        return 1
    print("post-bump steps: done")
    return 0


def main(argv: list[str] | None = None) -> int:
    # ★ argparse 的文本会被**打印**（--help），所以一律写 ASCII：
    #   Windows runner 默认 cp1252，非 ASCII 会在 `--help` 上 UnicodeEncodeError。
    parser = argparse.ArgumentParser(
        description="Complete the post-bump manual steps that bump_version.py does not do."
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--old", help="version before the bump, e.g. 0.13.0")
    parser.add_argument("--new", help="version after the bump, e.g. 0.13.1")
    parser.add_argument(
        "--report-draft",
        type=Path,
        default=None,
        help="release-report draft; when given, overwrites docs/releases/RELEASE_REPORT_<new>.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="read-only: report which steps are still pending; non-zero exit if any",
    )
    parser.add_argument(
        "--check-current",
        action="store_true",
        help=(
            "read-only gate mode: assert the pages that state the CURRENT version still state "
            "the version in pyproject.toml (no --old/--new needed)"
        ),
    )
    args = parser.parse_args(argv)

    if args.check_current:
        if args.old or args.new:
            parser.error("--check-current does not take --old/--new")
        return run_check_current(args.root)

    if not args.old or not args.new:
        parser.error("--old and --new are required (or use --check-current)")
    return run(args.root, args.old, args.new, args.report_draft, dry_run=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
