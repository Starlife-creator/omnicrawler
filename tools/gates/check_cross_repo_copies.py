"""跨仓副本等价性：市场仓里的同名工具必须与主仓**判据等价**。

## 这个门禁解决什么

市场仓为了「不依赖主仓的 `omnicrawler` 包」，把几个工具**内联复制**了一份：

| 脚本 | 主仓 | 市场仓 |
|---|---|---|
| `check_component_recipes.py` | `tools/gates/` | `tools/` |
| `component_deps_lock.py` | `tools/market/` | `tools/` |

**内联本身是合理的**：CI 只 checkout 市场仓，不能 import 主仓代码。

**但后果是**：改了主仓那份、忘了改市场仓那份，两边就各说一套判据。
本会话我真实踩过 —— 改了主仓的 `check_component_recipes.py`，
CI（用市场仓那份）仍按旧判据跑，直到推送后才暴露。

## 判据：为什么不用「字节相同」

两份**注定不同**：市场仓那份要内联 `load_recipe` / `RecipeError`，
主仓那份 import 它们。要求字节相同会立刻误报。

所以判据是**结构化比对**：

1. **常量等价**：允许的平台/架构/ABI 白名单必须一致
2. **函数集合等价**：两边都应提供的核心函数必须都在
3. **行数差异有界**：内联版比主仓版长是正常的，但差距超过阈值说明分叉严重

任一条不满足就判红，并指出**差在哪**。

## 边界（如实说明）

这**不能证明语义完全等价** —— 那需要跑同一批输入比对输出。
它证明的是「结构没有明显分叉」，能拦住「改了一边忘了另一边」这类事故。
真正的等价性靠的是：两边都跑同一个 `components-runnable` CI 工作流。

## 用法

```powershell
python tools/gates/check_cross_repo_copies.py
```
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

#: 主仓 → 市场仓的相对路径对。只登记「设计上应判据等价」的副本。
_PAIRS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "tools/gates/check_component_recipes.py",
        "../OmniCrawler-market/tools/check_component_recipes.py",
        (
            # 两边都必须认的平台名（写死在判据里的白名单）
            "windows",
            "linux",
            "macos",
            "aarch64",
            "x86_64",
        ),
    ),
    (
        "tools/market/component_deps_lock.py",
        "../OmniCrawler-market/tools/component_deps_lock.py",
        ("deps.lock.json", "requires_dist", "python_version"),
    ),
)

#: 内联版比主仓版长多少算「分叉严重」。内联 `load_recipe` 大约 +100 行。
_MAX_LINE_GROWTH = 180


def _source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{BAD_MARK} 无法读取 {path}：{exc}") from exc


def _defined_names(text: str) -> set[str]:
    """提取文件里定义的所有顶层函数/类名。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def _line_count(text: str) -> int:
    return len(text.splitlines())


#: 判据相关的标识提示词：出现在这些名字附近的小写标识算「判据白名单」。
_JUDGE_TOKENS_HINT = frozenset(
    {
        "windows",
        "linux",
        "macos",
        "darwin",
        "freebsd",
        "win32",
        "win_amd64",
        "win_arm64",
        "manylinux",
        "musllinux",
        "macosx",
        "aarch64",
        "arm64",
        "x86_64",
        "amd64",
        "cp310",
        "cp311",
        "cp312",
        "cp313",
        "cp313t",
    }
)


def _whitelist_tokens(text: str) -> set[str]:
    """提取文件里所有字符串字面量（用于比对判据白名单是否一致）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def check_pair(
    main_path: Path,
    market_path: Path,
    markers: tuple[str, ...],
) -> list[str]:
    """检查一对副本。返回问题列表（空＝通过）。"""
    problems: list[str] = []

    if not main_path.is_file():
        return [f"主仓文件不存在：{main_path}"]
    if not market_path.is_file():
        return [f"市场仓副本不存在：{market_path}"]

    main_text = _source(main_path)
    market_text = _source(market_path)
    label = market_path.name

    # ── ① **判据白名单必须逐项一致** ──────────────────────────────────
    #
    # 早先我只检查「markers 在两边各自存在」—— 那拦不住真正的分叉：
    # 主仓加了 `"freebsd"` 而副本没有，两边都仍然「包含 windows/linux」，
    # 旧判据判通过。**反向断言当场暴露了这个缺陷。**
    #
    # 现在改为：把两边所有**疑似判据白名单**的字符串集合提取出来，比对差集。
    main_names = _whitelist_tokens(main_text)
    market_names = _whitelist_tokens(market_text)
    only_main = sorted(main_names - market_names)
    only_market = sorted(market_names - main_names)
    # 只报「看起来像平台/架构/ABI 标识」的差异，避免把内联实现里的
    # 局部变量名当成判据分叉。
    suspicious = [
        name
        for name in only_main
        if name in _JUDGE_TOKENS_HINT
        or (name.isupper() and name.isascii() and len(name) > 2)
    ]
    if suspicious:
        problems.append(
            f"{label}: 主仓有、市场仓副本没有的判据标识 {suspicious} —— "
            "改主仓时必须同步改副本"
        )
    # **只报「像判据标识」的差异**：平台/架构/ABI 名，或全大写常量名。
    # 错误文案的差异（如「配方必须是 JSON 对象」vs「配方不是合法 JSON：」）
    # 不属于判据分叉 —— 两边对同一份坏数据都会拒绝，只是措辞不同。
    # 把文案算进去会让这条门禁变成噪音源，而噪音会被忽略。
    suspicious_back = [
        name
        for name in only_market
        if name in _JUDGE_TOKENS_HINT
        or (name.isupper() and name.isascii() and len(name) > 2)
    ]
    if suspicious_back:
        problems.append(
            f"{label}: 市场仓副本有、主仓没有的判据标识 {suspicious_back} —— "
            "两边判据已分叉"
        )

    # 保留原有的「markers 必须存在」检查（防清单本身过期）
    missing = [m for m in markers if m not in market_text]
    if missing:
        problems.append(
            f"{label}: 市场仓副本缺少这些关键标识 {missing} —— "
            "主仓那边可能加了新判据，副本没跟上"
        )

    # ── ② 核心函数集合：主仓有的，市场仓也该有（内联版可能改实现） ──
    main_names = _defined_names(main_text)
    market_names = _defined_names(market_text)
    # 只比较「两边都像判据主体」的函数：名字以 check_ / _check 开头
    main_checks = {n for n in main_names if n.startswith(("check_", "_check"))}
    market_checks = {n for n in market_names if n.startswith(("check_", "_check"))}
    lost = sorted(main_checks - market_checks)
    if lost:
        problems.append(
            f"{label}: 主仓有、市场仓副本没有的检查函数 {lost} —— "
            "副本漏了判据"
        )

    # ── ③ 行数增长有界 ───────────────────────────────────────────────
    delta = _line_count(market_text) - _line_count(main_text)
    if delta > _MAX_LINE_GROWTH:
        problems.append(
            f"{label}: 市场仓副本比主仓多 {delta} 行（上限 {_MAX_LINE_GROWTH}）"
            " —— 内联 `load_recipe` 之后还差这么多，说明分叉严重"
        )

    return problems


def _managed_names(path: Path) -> set[str]:
    """从主仓文件里提取它管理的「名字集合」（用于比对白名单是否一致）。"""
    text = _source(path)
    return set(re.findall(r'"([a-z0-9_.]+)"', text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查主仓与市场仓的副本工具是否判据等价",
    )
    parser.add_argument("--root", default="", help="主仓根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    problems: list[str] = []
    checked = 0

    for main_rel, market_rel, markers in _PAIRS:
        main_path = root / main_rel
        market_path = (root / market_rel).resolve()
        if not market_path.is_file():
            # 市场仓没 checkout（如 CI 单仓场景）⇒ 跳过，不判红
            print(f"  跳过 {Path(market_rel).name}：市场仓不可达")
            continue
        checked += 1
        problems.extend(check_pair(main_path, market_path, markers))

    # **防空集恒真**：一对都没比对上，说明路径写错或市场仓改名了。
    if checked == 0:
        print(
            f"{BAD_MARK} 没有比对任何一对副本 —— "
            "路径可能已失效（空集会让判据恒真）"
        )
        return 1

    if problems:
        print(f"{BAD_MARK} 跨仓副本不等价：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\n市场仓为了不依赖主仓包，内联复制了这些工具。"
            "改主仓那份时**必须同步改市场仓那份**，否则 CI 用旧判据跑。"
        )
        return 1

    print(f"{OK_MARK} {checked} 对跨仓副本判据等价（关键标识与检查函数齐全）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
