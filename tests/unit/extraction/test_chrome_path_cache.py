"""页面框架判据的缓存：同一份 HTML 里同一路径会被查很多次。

## 为什么要钉这条

detect_repeating_patterns 在给每个 pattern 打分时都会调 _is_chrome_path，
而相同父路径在同一份页面里重复度极高。实测（10 层深路径，每段 3 个类名）：

    命中缓存：0.046 us/次
    未命中  ：16.97 us/次      → 366 倍

★ 判据不是「看有没有 lru_cache 装饰器」（那是白盒、改实现就失效），
而是**实测同一路径重复查询显著快于每次换新路径**。
装饰器换成手写缓存也能通过；缓存被删掉就会红。

★ 用**深路径**测量：浅路径本身只要 2.7 us，缓存省下的绝对值太小，
  比值在噪声里（我曾用短路径写这条，实测只有 4.7 倍、断言 5 倍直接假红）。
"""

from __future__ import annotations

import timeit

from omnicrawler.extraction.intelligent_scraper.pattern_detector import _is_chrome_path


def test_repeated_lookup_is_cached() -> None:
    # 深路径：10 段 × 每段 3 个类名，接近真实页面的侧边栏/导航层级
    deep = " > ".join(f"div.c{i}a.c{i}b.c{i}c" for i in range(10))
    _is_chrome_path(deep)  # 冷启动一次（填缓存）

    cached = timeit.timeit(lambda: _is_chrome_path(deep), number=20000) / 20000

    counter = iter(range(10**9))

    def varying() -> bool:
        return _is_chrome_path(deep + str(next(counter)))

    uncached = timeit.timeit(varying, number=2000) / 2000
    assert cached < uncached / 5, (
        "重复查询没有变快（缓存失效了？）："
        f"cached={cached * 1e6:.3f}us uncached={uncached * 1e6:.3f}us"
    )


def test_cache_does_not_change_the_answer() -> None:
    """缓存不能改变语义：同一路径反复问，答案必须一致。"""
    cases = [
        ("body > nav > ul", True),
        ("body > div.main-content > ul", False),
        ("body > div.w3-sidebar > div", True),
    ]
    for path, expected in cases:
        for _ in range(3):
            assert _is_chrome_path(path) is expected, path
