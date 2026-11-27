"""**穷举守卫**：`site_inspector` 里每一张信号表的**每一个标记**，
单独出现在散文里时都不得触发该信号。

## 为什么要表驱动

2026-11-27 一天内发现 **8 例**同一缺陷：

    「某个名字在文本里出现过」 被当成 「页面在用它」

| 散文里写的 | 旧判定 |
|---|---|
| Use IntersectionObserver to ... | `pagination=(infinite-scroll,)` |
| OAuth 2.0 is a delegation protocol | `auth=(federated-login,)` |
| data-reactroot was used in v15 | `frameworks=(react/next,)` |
| Legacy apps used forum.php | `cms=(discuz,)` |

前几轮是**逐条修**的。逐条修的问题是：**新增一个标记时没人记得加测试**。
所以这里改成表驱动 —— 直接遍历模块里的标记表，新加标记自动被覆盖。

## 判据（两侧都要）

1. **散文里出现 ⇒ 不触发**（本文件上半部分）；
2. **可执行上下文里出现 ⇒ 触发**（下半部分）。

只做第 1 条会把判据改成一味不触发（恒假），只做第 2 条就是原来的 bug。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import (
    CMS_MARKERS,
    FEDERATED_LOGIN_MARKERS,
    FRAMEWORK_MARKERS,
    INFINITE_SCROLL_MARKERS,
    inspect_result,
)
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _inspect(body: str):
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog())


def _all_named_markers() -> list[tuple[str, str, str]]:
    """`(表名, 信号名, 标记)` 三元组，供 pytest 展开成独立用例。"""
    cases: list[tuple[str, str, str]] = []
    for table_name, table in (
        ("cms", CMS_MARKERS),
        ("frameworks", FRAMEWORK_MARKERS),
    ):
        for signal_name, markers in table.items():
            for marker in markers:
                cases.append((table_name, signal_name, marker))
    for marker in INFINITE_SCROLL_MARKERS:
        cases.append(("pagination", "infinite-scroll", marker))
    for marker in FEDERATED_LOGIN_MARKERS:
        cases.append(("authentication", "federated-login", marker))
    return cases


_CASES = _all_named_markers()


def test_tables_are_not_empty() -> None:
    """★ 空集合会让下面所有断言恒真 —— 必须先钉住这一点。"""
    assert len(_CASES) >= 15, f"标记表太小，疑似被清空：{len(_CASES)}"
    table_names = {case[0] for case in _CASES}
    assert table_names == {"cms", "frameworks", "pagination", "authentication"}, table_names


@pytest.mark.parametrize(("table", "signal", "marker"), _CASES)
def test_marker_in_prose_does_not_trigger(table: str, signal: str, marker: str) -> None:
    """★ 穷举：**每一个**标记单独出现在散文里，都不得触发它所属的信号。

    这条会把「新增标记时忘了考虑散文误报」直接抓出来。
    """
    body = f"<html><body><article><p>This text mentions {marker} in passing.</p></article></body></html>"
    report = _inspect(body)
    assert signal not in getattr(report, table), (
        f"标记 {marker!r} 只出现在散文里，却触发了 {table}.{signal}：{getattr(report, table)}"
    )


@pytest.mark.parametrize(("table", "signal", "marker"), _CASES)
def test_marker_in_executable_context_does_trigger(table: str, signal: str, marker: str) -> None:
    """★ 反方向穷举：同一个标记放进可执行上下文，必须触发。

    没有这条，上面那条可以用「一律返回 False」通过。
    """
    body = f'<html><body><div class="{marker}">x</div></body></html>'
    report = _inspect(body)
    assert signal in getattr(report, table), (
        f"标记 {marker!r} 在 class 属性里却未触发 {table}.{signal}：{getattr(report, table)}"
    )
