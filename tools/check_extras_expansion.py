"""Verify that the aggregate extras (full / full-macos) are exactly the expansion of
the feature extras, in declaration order.

## 问题

`full` 与 `full-macos` 是**手写**展开的：40 / 39 行逐字复制 20 个功能 extra 的所有条目。
每条依赖写了两遍。改动一个功能 extra 时，没有任何门禁提醒「`full` 也要跟着改」；
`tests/unit/utils/test_dependency_extras_contract.py` 只比对**集合**（按包名归一），
所以顺序漂移（例如 `curl_cffi` 被排到列表末尾）在测试里完全不可见。

## 判据

`pyproject.toml` 的 20 个功能 extra 是唯一真源，聚合 extra 必须**逐条等于**展开结果：

- 展开序 = 按 `FEATURE_EXTRAS` 的声明顺序，把每个功能 extra 的条目依次拼接，
  同一个包只保留首次出现（`lxml`、`openpyxl` 各出现两次）。
- `full` 必须与该展开序**逐字符一致**（顺序也算：顺序一变，审查 diff 就变噪音）。
- `full-macos` 必须等于「去掉 `paddleocr` / `paddlepaddle`，并在原 `numpy` 之后
  插入 `opencv-python-headless>=4.10,<6`」后的同一序列。
- 功能 extra 缺失、展开为空、聚合 extra 缺失 ⇒ 红（空集合会让所有断言恒真）。

## 为什么是「比对字面量」而不是「从展开结果生成」

`pyproject.toml` 是静态 TOML，setuptools 不认宏。任何「生成」都得先跑脚本、再让
构建工具读结果，构建链就多了一个隐式前置步骤 —— `uv sync`、`pip install -e .`、
打包脚本都要么踩空、要么各自再实现一遍。真源负责**声明**，门禁负责**对齐**：
漂移时指名是哪一项、期望什么。
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 功能 extra 的声明顺序（= pyproject.toml 里的书写顺序）。聚合 extra 必须照此展开。
FEATURE_EXTRAS: tuple[str, ...] = (
    "html",
    "pdf",
    "browser",
    "selenium",
    "async-http",
    "ai",
    "tls",
    "streams",
    "distributed",
    "scrapy",
    "ocr-tesseract",
    "ocr-paddle",
    "ocr-captcha",
    "crawl4ai",
    "gui",
    "security",
    "storage",
    "postgresql",
    "search",
    "document",
)

AGGREGATE_EXTRAS: tuple[str, ...] = ("full", "full-macos")

# macOS 上没有稳定的 paddle wheel：这两个包换成 headless OpenCV 变体（C-#14）。
MACOS_EXCLUDED: frozenset[str] = frozenset({"paddleocr", "paddlepaddle"})
MACOS_REPLACEMENT = "opencv-python-headless>=4.10,<6"
MACOS_REPLACEMENT_AFTER = "numpy"

_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def requirement_name(requirement: str) -> str:
    """Return the normalised distribution name of a requirement specifier."""
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise ValueError(f"cannot parse a distribution name from {requirement!r}")
    return match.group(0).lower().replace("_", "-")


def load_extras(root: Path) -> dict[str, list[str]]:
    path = root / "pyproject.toml"
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    extras = document.get("project", {}).get("optional-dependencies", {})
    return {name: list(items) for name, items in extras.items()}


def expand_feature_extras(extras: dict[str, list[str]]) -> list[str]:
    """Expand the feature extras in declaration order, keeping first occurrences."""
    expanded: list[str] = []
    seen: set[str] = set()
    for name in FEATURE_EXTRAS:
        for requirement in extras.get(name, []):
            key = requirement_name(requirement)
            if key in seen:
                continue
            seen.add(key)
            expanded.append(requirement)
    return expanded


def expected_macos(full_expansion: list[str]) -> list[str]:
    """Drop the paddle packages and add the headless OpenCV variant after numpy."""
    kept = [item for item in full_expansion if requirement_name(item) not in MACOS_EXCLUDED]
    anchor = next(
        (index for index, item in enumerate(kept) if requirement_name(item) == MACOS_REPLACEMENT_AFTER),
        None,
    )
    if anchor is None:
        kept.append(MACOS_REPLACEMENT)
    else:
        kept.insert(anchor + 1, MACOS_REPLACEMENT)
    return kept


def _compare(extra: str, actual: list[str], expected: list[str]) -> list[str]:
    problems: list[str] = []
    actual_names = [requirement_name(item) for item in actual]
    expected_names = [requirement_name(item) for item in expected]

    if actual_names != expected_names:
        position = next(
            (
                index
                for index, (got, want) in enumerate(zip(actual_names, expected_names, strict=False))
                if got != want
            ),
            min(len(actual_names), len(expected_names)),
        )
        got = actual[position] if position < len(actual) else "<end of list>"
        want = expected[position] if position < len(expected) else "<end of list>"
        problems.append(
            f"extra {extra!r}: entry #{position + 1} is {got!r} but the expansion says {want!r}"
        )
    elif actual != expected:
        position = next(
            index for index, (got, want) in enumerate(zip(actual, expected, strict=False)) if got != want
        )
        problems.append(
            f"extra {extra!r}: entry #{position + 1} is {actual[position]!r} "
            f"but the expansion says {expected[position]!r}"
        )
    return problems


def check(root: Path = REPO_ROOT) -> list[str]:
    extras = load_extras(root)

    missing = [name for name in FEATURE_EXTRAS if name not in extras]
    if missing:
        return [f"feature extra {name!r} is not declared in pyproject.toml" for name in missing]

    expansion = expand_feature_extras(extras)
    if not expansion:
        return ["empty selection is not a pass: the feature extras expand to nothing"]

    problems: list[str] = []
    for extra in AGGREGATE_EXTRAS:
        if extra not in extras:
            problems.append(f"aggregate extra {extra!r} is not declared in pyproject.toml")

    if "full" in extras:
        problems.extend(_compare("full", extras["full"], expansion))
    if "full-macos" in extras:
        problems.extend(_compare("full-macos", extras["full-macos"], expected_macos(expansion)))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    args = parser.parse_args(argv)
    problems = check(Path(args.root))
    if problems:
        for problem in problems:
            print(f"ERROR  {problem}", file=sys.stderr)
        print(f"extras expansion: {len(problems)} problem(s)", file=sys.stderr)
        return 1
    print("extras expansion: full and full-macos match the feature extra expansion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
