"""限额类消息里的字节数必须是人能读的形态。

为什么要有这个文件：第 19 轮把 `core.utils.human_bytes` 用到 `fetching` 层后，
其它层仍有裸字节数。这一轮扫描全仓，修了 3 处：

| 位置 | 原文 |
|---|---|
| `plugin_quota` | `今日网络字节已达配额 104857600` |
| `runtime/resources` | `Free disk space 52428800 bytes is below safety reserve 104857600 bytes` |
| `update_feed` | `可更新到 1.2.0（x.zip，52428800 字节）` |

这些读数用户要拿来做判断（要不要调配额 / 清理磁盘 / 下载）。
"""

from __future__ import annotations

import inspect

import pytest

from omnicrawler.core.utils import human_bytes


@pytest.mark.parametrize("module_path", [
    "omnicrawler.plugins.plugin_quota",
    "omnicrawler.runtime.resources",
    "omnicrawler.services.update_feed",
])
def test_module_uses_human_bytes(module_path: str) -> None:
    """三处都要引用共享实现，而不是自己拼接字节数。"""
    module = __import__(module_path, fromlist=["_"])
    source = inspect.getsource(module)
    assert "human_bytes(" in source, f"{module_path} 未使用 human_bytes"


def test_quota_message_shows_used_and_limit() -> None:
    """配额超限要同时给「已用」与「上限」，否则用户不知道差多少。"""
    from omnicrawler.plugins import plugin_quota

    source = inspect.getsource(plugin_quota)
    assert "已用" in source and "human_bytes(used_bytes)" in source


def test_disk_message_has_no_bare_byte_numbers() -> None:
    """磁盘告警不得出现 `... 52428800 bytes ...` 这种裸值。"""
    from omnicrawler.runtime import resources

    source = inspect.getsource(resources)
    assert "{usage.free} bytes" not in source
    assert "{self.minimum_free_disk_bytes} bytes" not in source


def test_human_bytes_output_is_what_users_see() -> None:
    """端到端确认渲染形态：100 MiB 显示成 `100.0 MiB`，不是 `104857600`。"""
    assert human_bytes(100 * 1024 * 1024) == "100.0 MiB"
    assert human_bytes(50 * 1024 * 1024) == "50.0 MiB"
