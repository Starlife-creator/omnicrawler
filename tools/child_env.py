"""子进程输出的 UTF-8 环境：一处共享，避免每个调用点各抄一遍。

**要解决的问题**：父进程若用 ``subprocess.run(..., encoding="utf-8")`` 解子进程输出，
就必须让子进程**按 UTF-8 写**。Windows 上 Python 子进程默认按控制台代码页写管道
（简体中文环境是 cp936/GBK），中文会被父进程解成一串 ``U+FFFD`` —— 于是
「代码做对了，断言却判红」。2026-11-14 的 :mod:`tools.self_verify` 就踩过这条。

**为什么合并而不是替换**：``env`` 参数会**整体覆盖**父环境。只传两个 UTF-8 变量会让
子进程丢掉 ``PATH`` / ``VIRTUAL_ENV`` / 代理设置，门禁会以另一种更难查的方式坏掉。
所以这里始终在 ``dict(os.environ)`` 的副本上叠加。

**为什么两个变量都设**：``PYTHONUTF8=1`` 让子解释器整体进入 UTF-8 模式（stdio、
文件系统、参数一致）；``PYTHONIOENCODING=utf-8`` 兜住只认后者的旧行为。只设一个都
可能在某个版本/某种启动方式下失效。

用法::

    from tools.child_env import utf8_env

    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=utf8_env())

注意：这是给「按 UTF-8 解输出」的调用方准备的。调 ``git`` / ``bash`` 这类**本来就按
UTF-8 写**的外部程序时不需要它，多设也无害（那不是它们读的变量）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = ["utf8_env"]

#: 叠加到子进程环境里的两项设置。改这里等于改所有调用方的行为。
UTF8_SETTINGS: Mapping[str, str] = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}


def utf8_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """返回「父环境 + UTF-8 设置」的新字典（不修改 ``os.environ``）。

    Args:
        overrides: 额外要覆盖的键值，最后叠加（用于测试与特殊场景）。

    Returns:
        可直接交给 ``subprocess.run(env=...)`` 的字典。
    """
    env = dict(os.environ)
    env.update(UTF8_SETTINGS)
    if overrides:
        env.update(overrides)
    return env
