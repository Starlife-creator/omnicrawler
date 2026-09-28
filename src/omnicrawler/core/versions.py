"""版本号比较的唯一实现（含非数字段时的既有语义）。

由来：同一套「按点切分、忽略非数字段」的比较此前在两处各写了一遍
（`gui/views/plugin_market_logic._version_tuple` 与
`gui/runner/env_checker.compare_versions` 的局部逻辑），而应用自更新
（`services/update_feed`）又需要第三处。**三处比较就必然漂移** ——
于是把判据提到 `core`，GUI 侧改为转出别名（`_version_tuple is version_key`），
`services` 直接使用，不再各写一份。

语义（与既有 GUI 行为**逐字一致**，不改判据）：
``"0.6.4"`` → ``(0, 6, 4)``；按 ``.`` 切分，**只保留纯数字段**。
注意非数字段是**整段连同位置一起丢弃**（不是当 0）：``"1.0.0-beta"`` → ``(1, 0)``，
所以它比 ``"1.0.0"``→``(1, 0, 0)`` **小**。实测确认，别按"语义化版本"的直觉理解它。
"""

from __future__ import annotations

__all__ = ["version_key", "is_newer"]


def version_key(value: str) -> tuple[int, ...]:
    """把版本字符串转成可比较的整数元组（忽略非数字段）。"""
    return tuple(int(part) for part in str(value).split(".") if part.isdigit())


def is_newer(candidate: str, current: str) -> bool:
    """``candidate`` 是否严格新于 ``current``。

    两者都取不到数字段时**不判为更新**（fail-closed：宁可漏报"有更新"，
    也不要把同一版本或解析不出的一对判成升级）。
    """
    left = version_key(candidate)
    right = version_key(current)
    if not left or not right:
        return False
    return left > right
