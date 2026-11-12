#!/usr/bin/env python
"""在 coverage 跟踪下跑 pytest，数据落盘后再退出，跳过解释器最终化。

为什么需要它
------------
`tests/conftest.py` 的逐用例 Qt 处置**必须**在每个用例结束后真正销毁窗口
（`tests/unit/gui/test_qt_session_cleanup.py` 用「Per-test widgets must be
deleted before the next theme change」守住这条不变式，因为下一个用例会改全局
主题）。但这份「销毁」在 coverage 跟踪下会把崩溃推迟到解释器最终化阶段：pytest
报告全部通过、`pytest_unconfigure` 与 `atexit` 都已跑完，进程随后 native 崩溃
（Linux exit 139 / Windows 0xC0000005，栈为 `Garbage-collecting` /
`<no Python frame>`）。

实测边界（2026-11-11）：**子集运行必现**（gui-and-browser job 的 3 个文件、
`tests/gui/test_main_window_contract.py` 单文件都崩），**全量 `pytest tests`
不出现**。因此只有子集 job 走本脚本；三条全量 test 腿继续用
`python -m coverage run -m pytest`。

试过但不能用的替代方案（都实测过退出码）：省略逐用例派发 DeferredDelete、
只派发待删控件自身的 DeferredDelete、逐用例 `gc.collect()`、
`shiboken6.delete()` 立即销毁、`sendPostedEvents` 前后暂停 coverage 跟踪、
把 QApplication 挂到 `sys` 上活过最终化——全部仍崩或破坏既有契约。

用法
----
    python tools/ci_coverage_pytest.py -q -o faulthandler_timeout=300 tests/...

参数原样传给 pytest；退出码与 pytest 一致，`.coverage` 数据文件正常生成。
"""

from __future__ import annotations

import os
import sys

from coverage import Coverage


def main(argv: list[str]) -> None:
    coverage = Coverage(config_file=True)
    coverage.start()
    code = 1
    try:
        import pytest

        code = int(pytest.main(argv))
    finally:
        coverage.stop()
        coverage.save()
        # 崩溃发生在解释器最终化阶段（数据已落盘之后），故跳过最终化返回。
        # flush 在这里是必须的：os._exit 不会替我们冲刷缓冲区。
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    main(sys.argv[1:])
