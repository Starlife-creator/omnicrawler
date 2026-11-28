"""XPath 语法校验（叶子模块，2026-11-28）。

## 为什么单独一个文件

`follow_xpath` 的消费者是 `sources/sources.py` 里的 `root.xpath(...)`，写错只在
**链接发现阶段**抛 `XPathEvalError` —— 那时已经抓了几页。

所以要在**配置校验**阶段就报。但这条判定需要 `lxml`，而：

- `core/config.py` 不能 import `lxml`：它是可选依赖，`core` 必须 import-safe
  （`check_architecture` 门禁守着）；
- `core/config.py` 也不能 import `sources/sources.py`：那会形成环
  （`sources.py` → `core.config`）。

**所以做成叶子模块**：不导入任何 `omnicrawler` 模块，`lxml` 在函数内惰性导入。
`core/config.py` 引它没有环，也没有可选依赖泄漏（`sources` 包不在
`FORBIDDEN_EXTERNALS` 里）。
"""

from __future__ import annotations

#: `lxml` 缺失时的**一次性**提示（避免每校验一个配置就记一条日志）。
_lxml_missing_warned = False


def xpath_syntax_errors(expression: str, label: str) -> list[str]:
    """返回 XPath 语法错误消息列表（空 = 通过）。

    **只编译不求值**：求值需要文档，而配置阶段没有文档。编译失败 = 语法失败，
    这类判据不产生假阳性。

    ★ 没装 `lxml` 时返回空（跳过）而不是报错：`lxml` 在 `html` / `gui` extra 里，
      不是基础依赖。让「最小安装」的用户连 `validate` 都跑不了，比漏检更糟。
    """
    global _lxml_missing_warned
    try:
        from lxml import etree
    except ImportError:  # pragma: no cover - 没装 html extra
        if not _lxml_missing_warned:
            _lxml_missing_warned = True
            import logging

            logging.getLogger(__name__).info(
                "未安装 lxml，跳过 XPath 语法校验（pip install omnicrawler[html]）"
            )
        return []

    try:
        etree.XPath(expression)
    except etree.XPathSyntaxError as exc:
        return [f"{label}: XPath 语法错误 {expression!r}（{exc}）"]
    return []
