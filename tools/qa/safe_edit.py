"""安全替换：**先验证语法，再写盘**。

## 这个工具解决什么

本会话我用批量正则/脚本改结构化代码，改坏 4 次：

| # | 我破坏了什么 | 怎样被发现 |
|---|---|---|
| ① | `OFFICIAL_VS_MIRROR_NOTES` 元组两个成员粘连成一个 | 测试报 `not enough values to unpack` |
| ② | import 语句插进多行 import 的括号中间 | ruff 语法错误 |
| ③ | `_()` 插入位置错（落在函数体内） | ruff 语法错误 |
| ④ | `builtin_entry_ok` 变量作用域错 | `UnboundLocalError` |

**四次都是写盘之后才发现。** 每一次都要多一轮「发现 → 回退 → 重来」。

其中 ①②③ 都能被 `ast.parse` 在**写盘前**拦住 —— 那只需要 1 毫秒。

## 怎么做

不提供「智能改写」（那需要理解语义，做不到也不该做），
只提供一道**最小但必要的闸门**：

```python
replace(path, old, new, count=1)   # 从工具里调用
```

它做三件事：
1. 断言 `old` 在文件里出现次数符合预期（防改错位置）
2. 把替换结果**先 `ast.parse` 一遍**
3. 通过才写盘

`.py` 之外的文件（`.md` / `.yml`）跳过语法检查，但仍做「出现次数」断言 ——
改错位置对它们同样是灾难。

## 用法

作为库：

```python
from tools.qa.safe_edit import replace

replace(Path("src/omnicrawler/x.py"), "旧代码", "新代码")
```

作为命令行（批量场景，减少写错的概率）：

```powershell
python tools/qa/safe_edit.py --file a.py --find "旧" --replace "新"
```

## 边界

* **不做语义检查**：`ast.parse` 只能担保「语法合法」。
  改成语法合法但逻辑错误的内容，它拦不住 —— 那要靠测试。
* **不做格式规范化**：替换后长什么样就是什么样，`ruff format` 是另一件事。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 需要做语法检查的后缀。
_SYNTAX_CHECKED = (".py",)


class SafeEditError(ValueError):
    """替换不安全，已放弃写入（文件未改动）。"""


def replace(
    path: Path,
    old: str,
    new: str,
    *,
    count: int = 1,
    expect: int = 1,
) -> None:
    """在 `path` 里把 `old` 换成 `new`，**验证语法后才写盘**。

    :param count: 最多替换几处
    :param expect: `old` 应该出现几次（不匹配就拒绝，防改错位置）

    **失败时文件保持原样** —— 这是这个工具存在的主要理由：
    写坏了再回退，比写之前先验证贵得多。
    """
    if not path.is_file():
        raise SafeEditError(f"文件不存在：{path}")

    original = path.read_bytes()
    text = original.decode("utf-8")

    found = text.count(old)
    if found != expect:
        raise SafeEditError(
            f"`old` 在 {path.name} 里出现 {found} 次（期望 {expect} 次）—— "
            "锚点不匹配就改写等于盲改，已放弃"
        )

    replaced = text.replace(old, new, count)
    if replaced == text:
        raise SafeEditError(f"替换没有产生变化：{path.name}")

    # 语法检查（只对 .py）：这是拦住 ①②③ 三类事故的那一步。
    if path.suffix in _SYNTAX_CHECKED:
        try:
            ast.parse(replaced, filename=str(path))
        except SyntaxError as exc:
            where = f"{exc.lineno}:{exc.offset}" if exc.lineno else "?"
            raise SafeEditError(
                f"替换后语法错误，**未写入**：{path.name}:{where} {exc.msg}"
            ) from exc

    path.write_bytes(replaced.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="安全替换：先验证语法，再写盘",
    )
    parser.add_argument("--file", required=True, help="要改的文件")
    parser.add_argument("--find", required=True, help="原文片段")
    parser.add_argument("--replace", required=True, help="替换成")
    parser.add_argument(
        "--expect",
        type=int,
        default=1,
        help="原文应出现几次（默认 1；不匹配就拒绝）",
    )
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_absolute():
        path = _ROOT / path

    try:
        replace(path, args.find, args.replace, expect=args.expect)
    except SafeEditError as exc:
        print(f"{BAD_MARK} {exc}")
        return 1

    print(f"{OK_MARK} 已替换 {path.name}（语法通过后才写盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
