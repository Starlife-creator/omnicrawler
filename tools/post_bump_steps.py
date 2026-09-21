#!/usr/bin/env python3
"""``bump_version.py`` 之后必须手工补的几步（那个工具明确不做的部分）。

用法::

    python tools/post_bump_steps.py --old 0.13.0 --new 0.13.1
    python tools/post_bump_steps.py --old 0.13.0 --new 0.13.1 --report-draft draft.md
    python tools/post_bump_steps.py --check --old 0.13.0 --new 0.13.1   # 只读：还有哪些没做

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
    """
    results: list[StepResult] = []
    drift: list[str] = []
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return [StepResult(_STATUS_ERROR, f"ERROR  docs/ not found under {root}")], drift
    metadata = re.compile(r"^" + re.escape(_VERSION_HEADER) + r"(\S+)", re.MULTILINE)
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        found = metadata.search(text)
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
    parser.add_argument("--old", required=True, help="version before the bump, e.g. 0.13.0")
    parser.add_argument("--new", required=True, help="version after the bump, e.g. 0.13.1")
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
    args = parser.parse_args(argv)
    return run(args.root, args.old, args.new, args.report_draft, dry_run=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
