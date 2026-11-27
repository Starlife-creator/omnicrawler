"""静态分析结果的「质量」判据 —— 决定要不要额外花浏览器开销。

## 为什么需要单独一个模块（2026-11-26 实测）

原来只有一个 `weak` 布尔量，判据是「没找到列表 / 列表项 < 3 / 落在页脚 / 分页靠 JS」。
它抓不到**另一类更隐蔽的失败**：静态 DOM 里确实有重复元素，于是「有结果」，
但那些元素根本不是业务数据 —— 抽出来是一堆兜底字段名。

实测（本轮）：

| 站点 | 静态分析结果 | 真实情况 |
|---|---|---|
| randomuser.me | 42 条记录，字段只有 `内容_span` | 首页只有导航与演示块，真数据靠 JS |
| scrapethissite/ajax | 3 条记录，字段 `链接文本/内容_p/正文/图片地址` 各 1 条 | 数据靠 AJAX |

两例的共同点：**记录数看着不少，字段名全是兜底的**（或以 `内容_` 开头）。
这种结果拿去跑，用户拿到的是一个结构正确、内容全是噪声的表 ——
比"没找到列表"更糟，因为它**看起来成功了**。

所以判据从「有没有」升级成「**像不像**」：

1. 有意义字段数（不是 `内容_<tag>` 兜底名的）为 0 ⇒ 一定弱；
2. 有意义字段占比低 ⇒ 弱；
3. 每个字段的填充率过低（多数记录取不到）⇒ 弱。
"""

from __future__ import annotations

from typing import Any

#: 兜底字段名前缀（`_classify_field` 认不出语义时的产物）。
FALLBACK_PREFIX = "内容_"


def meaningful_field_names(config: dict[str, Any]) -> list[str]:
    """返回**非兜底**的字段名（去掉 `内容_<tag>` 这类）。"""
    fields = ((config.get("extract") or {}).get("fields") or {})
    if not isinstance(fields, dict):
        return []
    return [name for name in fields if not str(name).startswith(FALLBACK_PREFIX)]


def quality_rank(config: dict[str, Any], report: dict[str, Any] | None) -> tuple[int, int, int]:
    """给一份「配置 + 试跑报告」打分，**越大越好**。

    三元组，按优先级比较：

    1. **有意义的字段个数** —— 这是最主要的判据。零个有意义字段的配置，
       记录再多也没有交付价值（用户看到的是一列列 `内容_span`）。
    2. **填充齐全的字段个数** —— 字段数一样时，取"每条记录都填上了"更多的。
    3. **记录数** —— 最后才看条数。

    刻意让记录数排最后：`randomuser.me` 静态 42 条 vs 渲染后可能只有 5 条，
    但前者全是噪声。若把条数排前面，渲染结果永远赢不了。
    """
    names = meaningful_field_names(config)
    filled = (report or {}).get("fields") or {}
    records = int((report or {}).get("records") or 0)
    complete = 0
    if records > 0:
        for name in names:
            if int(filled.get(name) or 0) >= records:
                complete += 1
    return (len(names), complete, records)


#: 站点抓取形态的结论标签（给用户看的一句话，不是内部枚举）。
VERDICT_STATIC = "静态可抓"
#: ★ 2026-11-27：SSR 站点（Next.js / Nuxt）的真实结论既不是"静态可抓"也不是
#:   "需浏览器渲染" —— 它**不需要浏览器**，但数据也不在 DOM 里，而在页面内嵌的
#:   JSON 载荷里。归到前两者任一个都会误导用户对"要不要开浏览器"的判断。
VERDICT_EMBEDDED = "静态可抓（页面内嵌数据）"
VERDICT_BROWSER = "需浏览器渲染"
VERDICT_LOGIN = "需要登录后才有内容"
VERDICT_NO_LIST = "未找到业务列表"


def verdict(
    *, config: dict[str, Any] | None, records: int, signals: tuple[str, ...], used_render: bool
) -> tuple[str, str]:
    """给出「这个站现在能不能抓」的一句话结论 + 理由。

    ★ 为什么要有这个（2026-11-26 用户提问）：
      原来成功时只打印「已验证: 42 条记录 / 字段 {...}」，失败时打印一段错误。
      用户真正想知道的是**这站属于哪一类、我该做什么** —— 例如「需浏览器渲染，
      已经替你切过去了」对比「需要登录，请用 record-actions 登一次」。

    Returns:
        ``(结论, 理由)``，理由为空串表示无需解释。
    """
    kinds = set(signals)
    # ★ 2026-11-26：`records > 0` **不等于**「抓到了东西」。
    #   实测 randomuser.me：静态 42 条记录，字段却只有一个兜底名 `内容_span`。
    #   按条数判会报「静态可抓」，而用户拿到的是 42 行噪声 —— 那是对结论撒谎。
    if config is None or records <= 0 or not meaningful_field_names(config):
        return VERDICT_NO_LIST, (
            "页面结构里没有找到可命名的业务字段（取到的都是兜底列名）；"
            "常见原因是数据靠 JS 异步加载、需要登录、或站点拒绝访问。"
        )
    source_kind = str(((config.get("source") or {}).get("kind")) or "")
    # ★ 2026-11-27：内嵌数据链路先判 —— 它的 `source.kind` 也是 `static_html`，
    #   但数据不在 DOM 里。混在"静态可抓"里会让用户以为去改选择器就能换字段。
    embedded = (config.get("extract") or {}).get("embedded_json")
    if isinstance(embedded, dict) and embedded.get("item_path"):
        return VERDICT_EMBEDDED, (
            "页面 DOM 里没有业务列表，但内嵌的 SSR 数据（如 __NEXT_DATA__）里有完整记录；"
            "运行时静态抓取即可，不必启动浏览器。字段规则用 source: embedded_json。"
        )
    if "login_form" in kinds and source_kind in {"static_html", "crawl"}:
        # 2026-11-26：判据从「有 form」收窄到「**登录表单**」。
        #   实测 books.toscrape 首页有个搜索框，于是所有带搜索的商品页都被判成
        #   「需要登录」—— 那是把最常见的正常页误判成有门槛。
        #   登录表单的判据：有 form，且 form 里有 password 输入。
        return VERDICT_LOGIN, "页面上有登录表单，数据可能在登录后才出现；请先用 record-actions 登一次（登录态会保存下来）。"
    if source_kind == "browser" or used_render:
        if "infinite_scroll" in kinds:
            return VERDICT_BROWSER, "内容随滚动追加，已配置滚动动作。"
        return VERDICT_BROWSER, "静态 HTML 里没有业务数据，已切换到浏览器渲染。"
    return VERDICT_STATIC, ""


def is_weak(config: dict[str, Any] | None, report: dict[str, Any] | None, *, total_fields: int) -> bool:
    """静态结果是否「弱到值得再花一次渲染」。

    `total_fields` 是这份配置里的字段总数（含兜底名），由调用方从同一份配置取。
    """
    if config is None:
        return True
    rank = quality_rank(config, report)
    meaningful, _complete, records = rank
    if meaningful == 0:
        # 一个有意义字段都没有 ⇒ 静态 DOM 里没有业务结构，不用再算比例
        return True
    if records <= 0:
        return True
    # 有意义字段占比过低（一半以上是兜底名）⇒ 同样弱
    if total_fields > 0 and meaningful * 2 < total_fields:
        return True
    return False
