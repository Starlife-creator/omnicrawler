"""host 归一化的**唯一真源**。

刻意做成仅依赖 stdlib 的叶子模块：任何层都能安全导入而不引入额外依赖。
`core.site_aliases` 与若干 fetching/sources 模块共用同一份语义，避免逐字副本漂移。

调用方若已经能导入 :mod:`omnicrawler.core.site_aliases`，可继续从那里取
``normalize_host``（site_aliases 会 re-export 本模块的实现）；跨层且需要
「别名层不可用时仍可用」的调用方，直接退化到本模块即可——它是 stdlib-only，
不存在导入失败的可能。
"""

from __future__ import annotations


def normalize_host(host: str) -> str:
    """归一化 host：去首尾空白、去末尾点、casefold；空输入返回 ``""``。

    与 services/metrics.py、security/policy.py 的 casefold 约定保持一致。
    """
    if not host:
        return ""
    return host.strip().rstrip(".").casefold()
