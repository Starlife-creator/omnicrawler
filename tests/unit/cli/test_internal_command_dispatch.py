"""内部命令的分发也必须「`--json` 位置无关」。

为什么要有这个文件：`_dispatch_internal` 在 argparse **之前**拦截内部命令，
判断写成 `argv[0] not in _INTERNAL_COMMANDS`。

实测（2026-11-24）：

```
omnicrawler   __component_worker__          ->  被识别，执行
omnicrawler   --json __component_worker__   ->  handled=False，落到 argparse
                                                => 报「未注册的命令」
```

与第 25 轮 `pdf` 分支是**同一个形态**：`--json` 位置无关是明确承诺，
但 `argv[0]` 判断没考虑前置 flag。

内部命令由 SDK / 子进程调用，静默失效比直接报错更难查。
"""

from __future__ import annotations

import pytest

from omnicrawler.cli._main import _INTERNAL_COMMANDS, _dispatch_internal


def _first_internal() -> str:
    return next(iter(_INTERNAL_COMMANDS))


def test_internal_command_is_recognized() -> None:
    """基线：不带前置 flag 时必须识别。"""
    with pytest.raises(SystemExit):
        _dispatch_internal([_first_internal()])


def test_leading_json_does_not_break_dispatch() -> None:
    """前置 `--json` 时同样必须识别 —— 这是本文件的核心断言。"""
    with pytest.raises(SystemExit):
        _dispatch_internal(["--json", _first_internal()])


def test_both_positions_behave_the_same() -> None:
    """直接比两条路径的「是否被处理」，而不是各自对常量断言。"""
    def handled(argv: list[str]) -> bool:
        try:
            return _dispatch_internal(list(argv))
        except SystemExit:
            return True  # 抛了 SystemExit 说明它去执行了

    plain = handled([_first_internal()])
    with_flag = handled(["--json", _first_internal()])
    assert plain == with_flag, f"--json 位置改变了分发结果：{plain} vs {with_flag}"


def test_non_internal_command_is_not_handled() -> None:
    """普通子命令不该被这条路径截走。"""
    assert _dispatch_internal(["status"]) is False


def test_leading_json_with_normal_command_is_not_handled() -> None:
    """前置 flag + 普通命令，同样不该截走。"""
    assert _dispatch_internal(["--json", "status"]) is False
