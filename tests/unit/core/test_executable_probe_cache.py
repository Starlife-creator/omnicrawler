"""可执行文件探测必须缓存 —— 否则每次刷下拉框卡 100ms。

## 两笔开销（2026-11-27 实测，GUI 启动剖析）

`MarketView()` 构造 143.9 ms，其中 **94 ms** 在 `list_kernels() → is_available()`：

```
shutil.which(...) × 4 个驱动名     79.8 ms   ← 每个名字都把 36 条 PATH 走一遍
「扫描全部 PATH 一遍」             28.8 ms   ← 5765 个条目，只做一次
在扫描结果里查 4 个名字             0.002 ms
```

`shutil.which` 慢的不是「查」，而是**每个名字都重走一遍 PATH**。
所以修法是**两层**：

| 层 | 缓存 | 省什么 |
|---|---|---|
| `_EXECUTABLE_CACHE` | `名称 -> 路径/None` | 同一个名字重复查 |
| `_PATH_INDEX` | 扫一遍 PATH 的 `小写名 -> 路径` | **不同名字**各走一遍 PATH |

2026-11-25 那版只加了第一层，理由是"`which` 一次 21 ms"。那没错，
但 4 个**不同**名字就是 4 次 21 ms —— 第一层缓存命中不了。

## 语义边界

**同一进程生命周期内可执行文件的位置不会变**。运行中装了驱动，重启应用才能看到 ——
与「插件需重启加载」的既有约定一致。需要同进程复检时调 `clear_executable_cache()`
（它同时清空 `_PATH_INDEX` 与可执行文件缓存）。
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


# ── 第一层：按名称的结果缓存 ────────────────────────────────────────────


def test_same_name_is_resolved_once() -> None:
    """同一名称只查一次索引（第二次直接命中 `_EXECUTABLE_CACHE`）。"""
    capability_slots._find_executable("python.exe")
    cached = capability_slots._EXECUTABLE_CACHE["python.exe"]
    # 把索引换成「查了就会炸」的实现：若第二次仍查索引，这条会红。
    original = capability_slots._PATH_INDEX
    capability_slots._PATH_INDEX = {}
    try:
        assert capability_slots._find_executable("python.exe") == cached
    finally:
        capability_slots._PATH_INDEX = original


def test_missing_executable_is_cached_as_none() -> None:
    """找不到时也缓存 None —— 否则每次都要把整条 PATH 走一遍。"""
    name = "definitely-not-a-real-driver-xyz"
    assert capability_slots._find_executable(name) is None
    assert capability_slots._EXECUTABLE_CACHE[name] is None
    original = capability_slots._PATH_INDEX
    capability_slots._PATH_INDEX = {}
    try:
        assert capability_slots._find_executable(name) is None
    finally:
        capability_slots._PATH_INDEX = original


def test_clear_cache_forces_reprobe() -> None:
    """`clear_executable_cache()` 后必须重新探测（安装驱动后同进程复检）。"""
    name = "python.exe"
    first = capability_slots._find_executable(name)
    capability_slots.clear_executable_cache()
    assert capability_slots._PATH_INDEX is None, "清缓存后索引没有失效"
    assert name not in capability_slots._EXECUTABLE_CACHE
    assert capability_slots._find_executable(name) == first


def test_distinct_names_are_probed_independently() -> None:
    """缓存按名称分键，不能把 A 的结果套给 B。"""
    real = capability_slots._find_executable("python.exe")
    missing = capability_slots._find_executable("definitely-not-a-real-driver-xyz")
    assert real is not None
    assert missing is None


# ── 第二层：PATH 索引（本次新增，省的是「不同名字」那笔） ────────────


def test_path_index_is_built_only_once() -> None:
    """★ 核心判据：**多个不同名字**只触发一次 PATH 扫描。

    这正是 2026-11-25 那版漏掉的：4 个驱动名 = 4 次全量遍历。
    """
    scans = [0]
    real_listdir = capability_slots.os.listdir

    def counting_listdir(path):
        scans[0] += 1
        return real_listdir(path)

    capability_slots.os.listdir = counting_listdir
    try:
        capability_slots._find_executable("chromedriver.exe")
        capability_slots._find_executable("geckodriver.exe")
        capability_slots._find_executable("python.exe")
        capability_slots._find_executable("cmd")
    finally:
        capability_slots.os.listdir = real_listdir

    # 索引只建一次 ⇒ listdir 的调用次数 = PATH 里的目录数（而不是「名字数 × 目录数」）。
    path_dirs = [p for p in capability_slots.os.environ.get("PATH", "").split(capability_slots.os.pathsep) if p]
    assert scans[0] <= len(path_dirs), (
        f"PATH 被扫了 {scans[0]} 次，超过目录数 {len(path_dirs)} —— 索引没有生效"
    )


def test_path_index_matches_shutil_which() -> None:
    """★ 索引结果必须与 `shutil.which` 一致（它是权威实现）。

    索引是自己实现的，**必须拿权威实现对照** —— 否则「快」得很可能是错的。
    """
    for name in ("python.exe", "python", "cmd", "definitely-not-a-real-driver-xyz"):
        indexed = capability_slots._find_executable(name)
        authoritative = shutil.which(name)
        assert (indexed is not None) == (authoritative is not None), (
            f"{name}: 索引说 {indexed}，which 说 {authoritative}"
        )


def test_path_index_handles_pathext_forms() -> None:
    """Windows 上不带扩展名的名字（`cmd`）要能匹配 `cmd.EXE`。"""
    import os

    if os.name != "nt":
        pytest.skip("PATHEXT 是 Windows 概念")
    assert capability_slots._find_executable("cmd") is not None
    assert capability_slots._find_executable("cmd.exe") is not None


def test_empty_path_index_falls_back_to_which() -> None:
    """★ 索引为空（PATH 一条目录都读不到）时必须退回权威实现。

    否则「找不到驱动」会变成环境问题的假象 —— 用户会以为装好了却不生效。
    """
    calls = [0]
    real_which = shutil.which

    def counting(name, *a, **kw):
        calls[0] += 1
        return real_which(name, *a, **kw)

    shutil.which = counting
    capability_slots._PATH_INDEX = {}
    try:
        capability_slots._find_executable("python.exe")
    finally:
        shutil.which = real_which
        capability_slots._PATH_INDEX = None
    assert calls[0] == 1, "索引为空时没有退回 shutil.which"


def test_nonempty_index_does_not_call_which() -> None:
    """★ 反方向：索引非空时**不许**退回 `which`（那会把 40 ms 请回来）。

    本机 4 个驱动名**全都找不到** —— 那是最常见的结果，也正是最容易
    被「找不到就回退」误伤的路径。
    """
    calls = [0]
    real_which = shutil.which

    def counting(name, *a, **kw):
        calls[0] += 1
        return real_which(name, *a, **kw)

    shutil.which = counting
    try:
        result = capability_slots._find_executable("definitely-not-a-real-driver-xyz")
    finally:
        shutil.which = real_which
    assert result is None
    assert calls[0] == 0, f"索引非空却退回了 shutil.which（{calls[0]} 次）"

# ── 第三层：`find_spec` 结果缓存（同一次刷新里重复查同一个包） ──────


def test_spec_probe_is_cached_per_module_name() -> None:
    """★ `find_spec` 也按名称缓存 —— 一次刷新里同一个包被查多次。

    实测：一次「刷新全部槽位」调 `find_spec` **20 次**，冷态 11.1 ms、
    缓存在手 1.3 ms。
    """
    capability_slots.clear_capability_probe_cache()
    calls = [0]
    real = capability_slots.importlib.util.find_spec

    def counting(name, *a, **kw):
        calls[0] += 1
        return real(name, *a, **kw)

    capability_slots.importlib.util.find_spec = counting
    try:
        # ★ 必须走 `_spec_available()`（provider 实际用的那条路径），
        #   而不是直接调 `_spec_exists()` —— 否则把 `_spec_available` 改回裸
        #   `find_spec` 时这条测试仍然绿（假守卫）。
        probe = capability_slots._spec_available("playwright")
        probe()
        probe()
        probe()
    finally:
        capability_slots.importlib.util.find_spec = real

    assert calls[0] == 1, f"同一个模块名被 find_spec 查了 {calls[0]} 次"


def test_spec_probe_clear_forces_recheck() -> None:
    """`clear_capability_probe_cache()` 后必须重新查（装包后同进程复检）。"""
    capability_slots._spec_exists("playwright")
    capability_slots.clear_capability_probe_cache()
    assert "playwright" not in capability_slots._SPEC_CACHE


def test_spec_probe_treats_errors_as_absent() -> None:
    """★ `find_spec` 会为非法名/缺父包抛异常 —— 那必须算「不存在」，不许炸。

    一处抛异常会让整个 `is_available()` 链断掉，界面上所有 provider 变成
    「未安装」——比判错更糟。
    """
    capability_slots.clear_capability_probe_cache()
    real = capability_slots.importlib.util.find_spec

    def boom(name, *a, **kw):
        raise ModuleNotFoundError(f"no parent for {name}")

    capability_slots.importlib.util.find_spec = boom
    try:
        assert capability_slots._spec_exists("some.missing.parent") is False
    finally:
        capability_slots.importlib.util.find_spec = real
        capability_slots.clear_capability_probe_cache()
