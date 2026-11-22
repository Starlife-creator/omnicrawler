"""探针脚本模板（由 tools/qa/probe.py 加载并填充参数）。

## 为什么单独一个文件

最初我把这段脚本写成 `probe.py` 里的嵌套 f-string —— 里面的 `{n}`、
`{label}` 与大括号转义互相冲突，改一次错一次。

**模板独立成文件后，它就是普通的 Python 源码**：可以直接读、直接改、
用 ruff 检查。`probe.py` 只负责把 `TARGETS` / `TEST_ARGS` 两行填进去。

占位符是 `__TARGETS__` 与 `__TEST_ARGS__`（不像 `{}` 那样需要转义）。
"""
from __future__ import annotations

import sys

#: 由 probe.py 替换成真正的列表。
TARGETS: list[str] = __TARGETS__  # noqa: F821 - 运行时由 probe.py 替换
TEST_ARGS: list[str] = __TEST_ARGS__  # noqa: F821 - 运行时由 probe.py 替换

#: 每个目标被调用的次数。
COUNTS: dict[str, int] = {}


def _shorten(value: object, limit: int = 120) -> str:
    """把值渲染成一行短文本，避免把整个对象树打出来。"""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - repr 失败不该让探针崩
        text = "<repr 失败>"
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _resolve(spec: str) -> tuple[object, str, object]:
    """把 `module:Class.method` 解析成 `(宿主对象, 属性名, 原函数)`。"""
    module_name, _, attribute = spec.partition(":")
    parts = attribute.split(".")
    holder: object = __import__(module_name, fromlist=["_"])
    for part in parts[:-1]:
        holder = getattr(holder, part)
    leaf = parts[-1]
    return holder, leaf, getattr(holder, leaf)


def _install() -> list[tuple[object, str, object]]:
    """包装每个目标函数，返回可还原的登记表。"""
    registry: list[tuple[object, str, object]] = []
    for spec in TARGETS:
        try:
            holder, leaf, original = _resolve(spec)
        except Exception as exc:  # noqa: BLE001 - 解析失败就跳过该目标
            print(f"[probe] 无法解析 {spec}: {exc}", file=sys.stderr)
            continue
        if not callable(original):
            print(f"[probe] {spec} 不是可调用对象", file=sys.stderr)
            continue

        COUNTS[spec] = 0
        registry.append((holder, leaf, original))
        setattr(holder, leaf, _make_wrapper(original, spec))
    return registry


def _make_wrapper(original, label: str):
    """生成记录参数/返回/异常的包装函数。"""

    def wrapper(*args, **kwargs):
        COUNTS[label] += 1
        n = COUNTS[label]
        print(f"[probe #{n}] {label} 调用", file=sys.stderr)
        # 跳过 self/cls，避免打出整个对象
        for index, value in enumerate(args[1:] if args else ()):
            print(f"[probe #{n}]   arg[{index}] = {_shorten(value)}", file=sys.stderr)
        for key, value in kwargs.items():
            print(f"[probe #{n}]   {key} = {_shorten(value)}", file=sys.stderr)
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            print(
                f"[probe #{n}]   抛 {type(exc).__name__}: {_shorten(exc)}",
                file=sys.stderr,
            )
            raise
        print(f"[probe #{n}]   返回 {_shorten(result)}", file=sys.stderr)
        return result

    return wrapper


def main() -> int:
    _install()
    import pytest

    code = pytest.main([*TEST_ARGS, "-q", "--no-header"])
    print("", file=sys.stderr)
    for label, count in COUNTS.items():
        print(f"[probe] {label} 共被调用 {count} 次", file=sys.stderr)
    return int(code)


if __name__ == "__main__":
    sys.exit(main())
