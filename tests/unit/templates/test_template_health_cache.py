"""`validate_template` 的进程内缓存：`recommend()` 第二次起 166 ms → 6 ms。

## 为什么要有缓存

`recommend()` 对**每个**模板都跑一次 `validate_template`。45 个模板共 **41 ms**
（实测），而 `recommend()` 在一次 `templates inspect` 里只调一次、
在 GUI 里会随筛选**反复**调。

实测（scrapethissite 198 KB 页面）：

| 调用 | 耗时 |
|---|---|
| 第 1 次 | 166 ms |
| 第 2 次起 | **6 ms** |

## 为什么缓存是安全的

`validate_template` 是 `record.metadata` + `record.config` 的**纯函数** ——
不读盘、不看时间、不看网络。而 `discover()` 已经缓存了同一批 record 对象，
所以缓存键用 `template_id` 就够，**失效点只有 `discover()` 一处**。

## 判据（两条）

| 判据 | 断言 |
|---|---|
| **正确性**：缓存不改变结论 | 同一 record 调多次，结果必须与直接调 `validate_template` 相同 |
| **失效**：`discover(refresh=True)` 后必须重算 | 改坏一个模板文件再 refresh，必须变 `ok=False` |

★ 第二条最容易漏：缓存如果不在 discover 里清空，用户改了模板文件却仍看旧结论。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.templates.template_catalog import TemplateCatalog
from omnicrawler.templates.template_health import validate_template

_TEMPLATE = """
template_version: 1
template:
  id: demo/one
  name: Demo One
  category: demo
  description: 示例模板
  version: 1.0.0
  capabilities: [crawl]
  placeholders:
    seed_url: {label: 页面地址, required: true}
  verified_at: "2026-01-01"
project: {name: demo, workspace: work/demo}
source: {kind: static_html, seeds: ["{{seed_url}}"]}
extract: {mode: html, fields: {}}
"""


def _catalog(tmp_path: Path) -> tuple[TemplateCatalog, Path]:
    root = tmp_path / "templates"
    target = root / "demo" / "one.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(_TEMPLATE, encoding="utf-8")
    return TemplateCatalog(root), target


def test_cached_health_matches_direct_call(tmp_path: Path) -> None:
    """★ 正确性：缓存不能改变结论。"""
    catalog, _target = _catalog(tmp_path)
    record = catalog.discover()[0]
    direct = validate_template(record)
    cached_first = catalog._health(record)
    cached_second = catalog._health(record)
    assert cached_first == direct
    assert cached_second == direct


def test_cache_is_actually_hit(tmp_path: Path) -> None:
    """★ 有效性：第二次必须是**同一个对象**（真的走了缓存，不是重算后恰好相等）。"""
    catalog, _target = _catalog(tmp_path)
    record = catalog.discover()[0]
    first = catalog._health(record)
    second = catalog._health(record)
    assert first is second, "缓存没生效：两次拿到的是不同对象"


def test_discover_refresh_invalidates_the_cache(tmp_path: Path) -> None:
    """★ 失效：改了模板文件再 refresh，结论必须跟着变。

    缓存不清空的后果是「用户修好了模板，工具仍报旧错误」。
    """
    catalog, target = _catalog(tmp_path)
    record = catalog.discover()[0]
    assert catalog._health(record).ok is True

    # 把模板改坏（description 改成空白 —— `validate_template` 会判 `template.description is required`）
    broken = _TEMPLATE.replace("  description: 示例模板", '  description: "   "')
    target.write_text(broken, encoding="utf-8")

    refreshed = catalog.discover(refresh=True)[0]
    assert not refreshed.metadata.description.strip()
    assert catalog._health(refreshed).ok is False, (
        "discover(refresh=True) 后缓存未失效 —— 会一直报旧结论"
    )


def test_cache_is_cleared_not_just_overwritten(tmp_path: Path) -> None:
    """★ 删掉模板后缓存里不许残留（否则 id 复用时拿到别人的结论）。"""
    catalog, target = _catalog(tmp_path)
    record = catalog.discover()[0]
    catalog._health(record)
    assert "demo/one" in catalog._health_cache

    target.unlink()
    catalog.discover(refresh=True)
    assert catalog._health_cache == {}, catalog._health_cache
