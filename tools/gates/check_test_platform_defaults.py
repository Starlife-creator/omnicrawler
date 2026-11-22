"""测试 fixture 的默认值不得硬编码「本平台」的平台/架构。

## 这个门禁解决什么

2026-11-22：CI 的 macOS 腿连续几轮红 9~11 条，全部同一个根因——

```python
def _recipe(...):
    return {
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],       # ← 默认值硬编码
        "python_abi": ["cp313"],
    }

def test_something():
    check_compatibility(_recipe())          # 不传 architecture ⇒ 用当前平台比对
```

`check_compatibility` 会拿 `current_architecture()`（macOS runner 上是
`aarch64`）去比对配方里的 `x86_64` ⇒ 被判「不支持当前架构」。
于是**所有「本机应可装」的用例在 Apple Silicon 上全红**。

## 为什么本机发现不了

`current_architecture()` 的返回值**由执行环境决定**：
本机是 `x86_64`，CI 的 macOS 是 `aarch64`。这不是「没测」，
而是**单一环境永远不会覆盖这个维度**。

但其中有一类问题是**纯静态可查的**：

> 一个 helper 的默认值里写着具体平台/架构，而它的调用方
> 大多**显式传同一个值** —— 说明这个默认值是被外部假设的常量，
> 一旦换平台就同时错。

这正是本次事故的形态：`_recipe()` 默认 `x86_64`，而 31 处调用里
大量显式传 `architecture="x86_64"`。

## 判据

对一个**返回 dict 字面量的 helper 函数**（名字以 `_` 开头、
不是 `test_`），检查它的字面量里是否出现
`"platforms"/"architectures"` 等键且值为硬编码的平台/架构名。

若出现，**且该函数在同文件内被调用时不总是显式覆盖该键** ⇒ 判红。

## 为什么不用「禁止一切平台字面量」

351 处平台/架构字面量里绝大多数是**合法的**：

* `check_compatibility(recipe, platform="windows", architecture="x86_64")`
  —— 显式指定被测环境，与宿主无关；
* `test_platform_names_use_canonical_spelling`
  —— 就是要验 `darwin` 的归一，字面量是**被测对象**。

把它们一律禁掉会制造大量噪音，而噪音会被忽略。所以只盯
「**被调用方隐式继承**的默认值」这一种真正危险的形态。

## 用法

```powershell
python tools/gates/check_test_platform_defaults.py
```
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

#: 视为「平台/架构」的字面量（小写比对）。
_PLATFORM_LITERALS = frozenset(
    {"windows", "linux", "macos", "darwin", "win32", "x86_64", "aarch64", "arm64", "amd64"}
)

#: 这些键的字面量列表被视为「环境假设」。
_ENV_KEYS = frozenset({"platforms", "architectures", "platform", "architecture"})

#: 扫描范围。
_SCAN_ROOT = "tests"

#: 显式豁免：这些函数的硬编码默认值是**刻意**的（例如测归一化）。
_EXEMPT_FUNCTIONS: dict[str, str] = {
    # v2 的 `platform_matrix` 用例**刻意**同时声明两种架构，
    # 验的是「同平台不同架构用不同构件/ABI」这条判据本身。
    # 改成 current_architecture() 会让它对「双架构」的覆盖消失。
    "tests/unit/services/test_component_schema_v2.py::_matrix_recipe":
        "刻意声明 aarch64+x86_64，验 platform_matrix 的双架构分支",
    "tests/unit/services/test_component_recipe_install.py::_recipe":
        "刻意构造跨平台配方（含 darwin 别名），验装配与平台筛选逻辑",
    "tests/unit/services/test_component_recipe_raw.py::_recipe":
        "同 test_component_recipe_install：刻意构造跨平台配方",
    "tests/unit/cli/test_self_update.py::_write_manual_only_feed":
        "该 feed 刻意只声明 macos，验「无可用更新」分支",
    "tests/unit/tools/test_check_component_recipes.py::_base_recipe":
        "刻意构造单平台（macos）配方，验配方校验器对该平台的判据",
    "tests/unit/plugin/test_package_manifest.py::_component_dir":
        "刻意构造单平台单架构包，验包清单校验的拒绝路径",
}


def _literal_names(node: ast.AST) -> set[str]:
    """收集节点下所有字符串字面量（小写）。"""
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.add(child.value.strip().lower())
    return out


def _dict_env_keys(node: ast.Dict) -> dict[str, set[str]]:
    """从 dict 字面量里取出 `{键: 值里的平台名}`。"""
    found: dict[str, set[str]] = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        name = key.value.strip().lower()
        if name not in _ENV_KEYS:
            continue
        names = _literal_names(value) & _PLATFORM_LITERALS
        if names:
            found[name] = names
    return found


def find_problems(root: Path) -> tuple[list[str], int]:
    """返回 `(问题列表, 检查过的 helper 数)`。"""
    problems: list[str] = []
    checked = 0
    base = root / _SCAN_ROOT
    if not base.is_dir():
        return problems, checked

    for path in base.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        # 收集本文件里所有「调用时传了哪些关键字」。
        # **记录每一次调用传了哪些键**，而不是所有调用的并集。
        #
        # 早先用并集判断「调用方是否总覆盖」—— 只要**有一处**传了
        # `architectures="x86_64"`，并集就含该键，于是整条判定被放行，
        # 而实际有 6/7 次调用隐式继承默认值。反向断言当场暴露。
        call_records: dict[str, list[set[str]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                keys = {kw.arg for kw in node.keywords if kw.arg}
                call_records.setdefault(node.func.id, []).append(keys)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name.startswith("test_") or not node.name.startswith("_"):
                continue
            qualified = f"{path.relative_to(root).as_posix()}::{node.name}"
            if qualified in _EXEMPT_FUNCTIONS:
                continue

            # 找函数体里返回的 dict 字面量。
            env_keys: dict[str, set[str]] = {}
            for child in ast.walk(node):
                if isinstance(child, ast.Dict):
                    env_keys.update(_dict_env_keys(child))
            if not env_keys:
                continue

            checked += 1
            # **三平台全列是安全的**：那不是「假设本平台」，而是「哪都能用」。
            # 只对**排他性**声明（少列了某个平台/架构）判红。
            all_platforms = {"windows", "linux", "macos"}
            if env_keys.get("platforms", set()) >= all_platforms:
                env_keys.pop("platforms", None)
            elif env_keys.get("platforms", set()) & {"darwin"}:
                env_keys["platforms"] = env_keys["platforms"]  # darwin 是别名，保留
            if not env_keys:
                continue

            # **只要存在一次「隐式继承默认值」的调用就判红**。
            records = call_records.get(node.name, [])
            implicit = [r for r in records if not set(env_keys) <= r]
            if not implicit:
                continue
            detail = "、".join(f"{k}={sorted(v)}" for k, v in sorted(env_keys.items()))
            problems.append(
                f"{path.relative_to(root).as_posix()}::{node.name} "
                f"默认值硬编码 {detail}；{len(implicit)}/{len(records)} 次调用"
                "隐式继承该默认值（换平台即同时失效）"
            )
    return problems, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查测试 helper 的默认值是否硬编码本平台的平台/架构",
    )
    parser.add_argument("--root", default="", help="仓库根（缺省＝本文件上两级）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ROOT
    problems, checked = find_problems(root)

    if problems:
        print(f"{BAD_MARK} 这些测试 helper 的默认值会绑定到某一平台/架构：")
        for item in problems:
            print(f"  - {item}")
        print(
            "\n这类默认值在换平台（macOS arm64 / Linux）时会**同时失效**，"
            "而本机永远测不出来。改用 `current_platform()` / "
            "`current_architecture()`，或在每个调用点显式传值。"
        )
        return 1

    print(f"{OK_MARK} {checked} 个 helper 的默认值不含硬编码平台/架构")
    return 0


if __name__ == "__main__":
    sys.exit(main())
