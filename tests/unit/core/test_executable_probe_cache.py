"""可执行文件探测必须缓存 —— 否则每次刷下拉框卡 100ms。

为什么要有这个文件（2026-11-25 实测）：

本机 PATH 有 36 条目录，`shutil.which("chromedriver.exe")` 实测 **21 ms**
（走完 36 个目录的 `nt._path_exists`）。而 `slot_combo_items()` 每次刷新
下拉框都对每个 provider 调 `is_available()`：

```
6 个槽位 × 18 个条目 = 98.3 ms，其中 84 ms 全花在 4 次 shutil.which 上
缓存后二次调用        = 14.7 ms（which 调用 0 次）
```

缓存的语义边界：**同一进程生命周期内可执行文件的位置不会变**。
用户在运行中装了驱动，重启应用才能看到 —— 与「插件需重启加载」的既有约定一致。
需要同进程复检时调 `clear_executable_cache()`。
"""

from __future__ import annotations

import shutil

import pytest

from omnicrawler.core import capability_slots


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    capability_slots.clear_executable_cache()
    yield
    capability_slots.clear_executable_cache()


def test_same_name_is_resolved_once(monkeypatch) -> None:
    """同一名称只查一次 `shutil.which`。"""
    calls: list[str] = []
    real = shutil.which

    def counting(name, *args, **kwargs):
        calls.append(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", counting)

    capability_slots._find_executable("chromedriver.exe")
    capability_slots._find_executable("chromedriver.exe")
    capability_slots._find_executable("chromedriver.exe")

    assert calls.count("chromedriver.exe") == 1, (
        f"同一名称被重复探测 {calls.count('chromedriver.exe')} 次"
    )


def test_missing_executable_is_cached_as_none(monkeypatch) -> None:
    """找不到时也缓存 None —— 否则每次都要把 36 个目录走一遍。"""
    calls = [0]
    monkeypatch.setattr(shutil, "which", lambda name, *a, **kw: calls.__setitem__(0, calls[0] + 1) or None)

    assert capability_slots._find_executable("definitely-not-a-real-driver-xyz") is None
    assert capability_slots._find_executable("definitely-not-a-real-driver-xyz") is None
    assert calls[0] == 1, f"未命中结果没有缓存（which 被调 {calls[0]} 次）"


def test_clear_cache_forces_reprobe(monkeypatch) -> None:
    """`clear_executable_cache()` 后必须重新探测（安装驱动后同进程复检）。"""
    calls = [0]
    monkeypatch.setattr(shutil, "which", lambda name, *a, **kw: calls.__setitem__(0, calls[0] + 1) or None)

    capability_slots._find_executable("some-driver-abc")
    capability_slots.clear_executable_cache()
    capability_slots._find_executable("some-driver-abc")
    assert calls[0] == 2, "清空缓存后没有重新探测"


def test_distinct_names_are_probed_independently(monkeypatch) -> None:
    """缓存按名称分键，不能把 A 的结果套给 B。"""
    monkeypatch.setattr(
        shutil, "which",
        lambda name, *a, **kw: "/fake/chromedriver.exe" if "chromedriver" in name else None,
    )
    assert capability_slots._find_executable("chromedriver.exe") is not None
    assert capability_slots._find_executable("geckodriver.exe") is None
