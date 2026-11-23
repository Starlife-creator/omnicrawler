"""CLI 错误提示的守卫：**建议必须与真实原因匹配**。

## 为什么这个测试存在（2026-11-23 的真实事故）

`_print_error_hint` 早先这么写：

```python
elif isinstance(exc, PermissionError):
    hints.append("请确认目标网站允许自动访问，或检查网络代理设置")
```

**任何** `PermissionError` 都得到这条建议。而我那天执行
`omnicrawler components list`，真实原因是 `components/.registry.lock`
不可写 —— 提示却让我去查网络代理。

**错误的建议比没有建议更糟**：它会把人（和 agent）往完全无关的方向带，
而且 agent 不会像人那样「感觉哪里不对」。

## 判据

1. 文件类 `PermissionError`（带 `filename`）必须**指出那个路径**，
   且**不得**出现网络/代理字样；
2. 网络类 `PermissionError`（无 `filename`）才给网络建议；
3. `FileNotFoundError` 必须提到路径或 `init`；
4. 每条建议都必须**具体**（不能是「请检查环境」这类空话）。

## 边界

不验文案措辞，只验「是否指向正确的排查方向」。这是行为测试，
不是字符串快照 —— 后者会在改措辞时无意义地红。
"""
from __future__ import annotations

import sys

import pytest

from omnicrawler.cli._main import _print_error_hint


def _hint_for(exc: Exception) -> str:
    """捕获 `_print_error_hint` 写到 stderr 的内容。"""
    import io

    buffer = io.StringIO()
    original = sys.stderr
    sys.stderr = buffer
    try:
        _print_error_hint(exc)
    finally:
        sys.stderr = original
    return buffer.getvalue().strip()


def test_file_permission_error_points_at_the_path() -> None:
    """**核心守卫**：文件权限错误必须指出路径，且不能建议查网络。

    真实场景：`components list` 报 `registry.lock` 不可写，
    早先却提示「检查网络代理设置」—— 方向完全错。
    """
    path = r"C:\Users\demo\.omnicrawler\components\.registry.lock"
    hint = _hint_for(PermissionError(13, "Permission denied", path))
    assert path in hint, f"没指出具体路径，agent 无从下手：{hint!r}"
    for irrelevant in ("代理", "proxy", "网站", "网络"):
        assert irrelevant not in hint, (
            f"文件权限错误却提到「{irrelevant}」—— 这会把人带向无关方向：{hint!r}"
        )


def test_network_permission_error_keeps_network_hint() -> None:
    """无 `filename` 的 PermissionError 才给网络类建议（保持原有用途）。"""
    hint = _hint_for(PermissionError("blocked by policy"))
    assert hint, "网络场景没有给任何建议"
    assert "网络" in hint or "代理" in hint or "网站" in hint, (
        f"网络场景的建议没指向网络：{hint!r}"
    )


def test_file_not_found_suggests_path_or_init() -> None:
    """配置缺失要么指路径，要么提示 `init` —— 两者都指向可操作动作。"""
    hint = _hint_for(FileNotFoundError(2, "No such file", "task.yaml"))
    assert "路径" in hint or "init" in hint, f"建议不可操作：{hint!r}"


def test_seeds_error_is_specific() -> None:
    """缺入口 URL 时要说清去哪填，而不是笼统「检查配置」。"""
    hint = _hint_for(ValueError("source.seeds 为空"))
    assert "seeds" in hint, f"没指向具体字段：{hint!r}"


def test_hint_is_never_empty() -> None:
    """兜底分支也必须给建议 —— 空提示等于没有诊断信息。"""
    hint = _hint_for(RuntimeError("某种未知错误"))
    assert hint, "未知异常没有给任何建议"


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(13, "denied", "/tmp/x/.lock"),
        FileNotFoundError(2, "missing", "/tmp/task.yaml"),
        ValueError("template 不存在"),
        ValueError("source.seeds 为空"),
    ],
)
def test_hint_always_names_a_concrete_next_step(exc: Exception) -> None:
    """每条建议都必须含**可执行的**指向（路径、命令或字段名）。"""
    hint = _hint_for(exc)
    concrete = ("omnicrawler", "路径", ".yaml", "seeds", "template", "/", "\\", "init")
    assert any(token in hint for token in concrete), (
        f"建议太笼统，agent 无法据此行动：{hint!r}"
    )
