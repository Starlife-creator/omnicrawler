"""网页分页链接、页码参数模式与内嵌 iframe 探测。"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any


def detect_pagination(html: str, url: str) -> dict[str, Any] | None:
    """检测页面分页模式。"""
    try:
        from lxml import html as lxml_html
    except ImportError:
        return None

    try:
        root = lxml_html.fromstring(html)
    except Exception:
        return None

    # 检测 URL 参数的页码模式
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)

    pagination_params = {"page", "p", "pg", "pagenum", "page_no", "pn", "offset", "start"}
    for key in pagination_params:
        if key in params:
            return {
                "type": "url_param",
                "param": key,
                "current_value": params[key][0],
                "description": f"URL 参数翻页: ?{key}=N",
            }

    # 检测链接中的翻页模式
    page_links = root.xpath("//a[contains(@href, 'page=') or contains(@href, '&p=') or contains(@href, '?p=')]")
    if page_links:
        href = page_links[0].get("href", "")
        for key in pagination_params:
            m = re.search(rf"[?&]{key}=(\d+)", href)
            if m:
                return {
                    "type": "url_param",
                    "param": key,
                    "example_href": href,
                    "description": f"链接参数翻页: {key}=N",
                }

    # 检测"下一页"按钮
    next_xpaths = [
        "//a[contains(text(), '下一页')]",
        "//a[contains(text(), 'Next')]",
        "//a[contains(@class, 'next')]",
        "//a[contains(@rel, 'next')]",
        "//button[contains(text(), '下一页')]",
        "//span[contains(@class, 'next')]/a",
        "//li[contains(@class, 'next')]/a",
    ]
    for xp in next_xpaths:
        elements = root.xpath(xp)
        if elements:
            href = elements[0].get("href", "")
            return {
                "type": "next_link",
                "xpath": xp,
                "example_href": href,
                "description": f"下一页链接: {xp}",
            }

    return None


#: 分页参数名的候选（与 `detect_pagination` 内部同一张表；这里用于**从 href 里认参数**）。
_PAGINATION_PARAMS: tuple[str, ...] = (
    "page", "p", "pg", "pagenum", "page_no", "pn", "offset", "start",
)


def _page_parameter_in(href: str) -> str:
    """从一条链接里认出页码参数名（``/x?page=2`` ⇒ ``page``）；认不出返回空串。"""
    for name in _PAGINATION_PARAMS:
        if re.search(rf"[?&]{name}=\d", href, re.IGNORECASE):
            return name
    return ""


def _max_page_number(html: str, parameter: str) -> int | None:
    """从页面自身取**最大页码**（`?page=2` … `?page=17`）；取不到返回 ``None``。

    ★ 为什么必须从页面里取：`source.pagination.end` 决定"翻到第几页"，而
      `sources.py` 的默认是 `end = start` —— **缺 `end` 的配置根本不会翻页**，
      运行结果只是"少了几页"，还是"成功"。所以这里宁可返回 `None`
      （调用方就**不写配置**、只给建议），也不猜一个数。
    ★ `rel="last"` 的链接 href 里同样带页码参数，所以一并被下面的扫描覆盖。
    """
    numbers = [
        int(match.group(1))
        for match in re.finditer(rf"[?&]{re.escape(parameter)}=(\d{{1,4}})", html, re.IGNORECASE)
    ]
    positive = [number for number in numbers if number >= 1]
    return max(positive) if positive else None


def _pagination_config(
    html: str, detected: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """把检测到的分页信号翻译成 **`core/pagination.py` 契约里的 `page` 形状**（走查 R5.2）。

    ★ **修正一处"配了等于没配"**：旧实现确实会写 `source.pagination`，但只写
      ``{type: page, parameter: X}`` —— **没有 `end`**。而 `sources.py` 的默认是
      ``end = start`` ⇒ 运行期只发第 1 页；`validate_pagination` 又只对**游标形状**
      要求 `next_path`，对页码形状不要求 `end` ⇒ **校验通过、只抓一页**，
      用户以为"翻页已处理"。这里保证 `end` 一定有值（取自页面自身链接）。

    ★ `detect_pagination` 产出的是 `url_param` / `next_link`，**都不是契约里的形状名**，
      所以必须显式翻译，不能原样写进去。

    ★ `next_link` 型**不生成"点击下一页"动作**（历史教训，保留原注释）：
      "下一页"就是同站 `<a href>`，由 ``source.discover`` 的**通用链接发现**跟进即可
      （`crawl` / `browser` 都参与，受 `crawl.max_depth` / `max_pages` / `same_host` 约束）。
      历史实现把它写成 ``browser.actions`` 的"点击下一页"，而 actions 对**每个**渲染页
      都执行 —— 入口页一点即跳走，第一页内容全丢（实测 quotes.toscrape.com/js：最终 URL
      变成 /js/page/2/、正文只剩未渲染骨架、0 条记录；分析期自校验不跑 actions，
      所以校验通过 10 条 —— 分析与运行结果不一致的根源就在这里）。
      ⇒ 只有当下一页链接**带页码参数**时，才翻译成页码式分页配置。

    Returns:
        ``(pagination 或 None, 面向用户的提醒)``。
    """
    if not detected:
        return None, []

    kind = str(detected.get("type") or "")
    if kind == "url_param":
        parameter = str(detected.get("param") or "")
    elif kind == "next_link":
        parameter = _page_parameter_in(str(detected.get("example_href") or ""))
    else:
        return None, []

    if not parameter:
        if kind == "next_link" and detected.get("xpath"):
            return None, ["已通过 source.follow_xpath 只跟进识别出的下一页链接，不遍历登录、导航或其他站内链接；仍受页面预算与安全边界限制。"]
        target = str(detected.get("xpath") or detected.get("example_href") or "")
        return None, [
            "检测到「下一页」链接，它不是页码参数地址 ⇒ **没有写入分页配置、也没有生成点击动作**"
            "（自动点「下一页」曾导致入口页内容全丢）。这类页面由 `crawl` 的**通用链接发现**跟进"
            "（同站链接，受 `crawl.max_depth` 限制）—— 确认能翻到时无需额外配置。"
            + (f"该链接定位：{target}；" if target else "")
            + "若链接发现翻不到，请用 `omnicrawler record-actions` 录制，"
            "或手工声明 `source.pagination`（页码式填 `parameter` + `end`）。"
        ]

    end = _max_page_number(html, parameter)
    if not end or end < 2:
        return None, [
            f"检测到分页（`{parameter}`），但页面上看不出共有多少页 ⇒ **没有写入** `source.pagination`"
            "（只写 `start` 不写 `end` 等于只抓第一页，是假配置）。"
            f"请手工补 `source.pagination: {{type: page, parameter: {parameter}, start: 1, end: N}}`。"
        ]

    notes = [
        f"检测到分页 ⇒ 已写入 `source.pagination`（页码式：`{parameter}` 第 1–{end} 页）。"
        f"`end={end}` 取自页面自身链接里的最大页码；实际页数更多时请调大它。"
    ]
    return {"type": "page", "parameter": parameter, "start": 1, "end": end, "step": 1}, notes


def _iframe_locators(html: str) -> tuple[str, ...]:
    """列出页面里 iframe 的**定位信息**（走查 R5.2：只说"内容在 iframe 里"不够用）。

    优先 ``#id`` → ``[name]`` → ``[src]`` → ``nth-of-type``，并带上 ``src``
    —— 用户据此才能在配置/录制里指到那个 frame。
    """
    try:
        from lxml import html as lxml_html

        root = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001 —— 定位信息是"锦上添花"，取不到就不给，不影响主流程
        return ()

    locators: list[str] = []
    for index, node in enumerate(root.xpath("//iframe"), 1):
        node_id = str(node.get("id") or "").strip()
        name = str(node.get("name") or "").strip()
        src = str(node.get("src") or "").strip()
        if node_id:
            where = f"iframe#{node_id}"
        elif name:
            where = f'iframe[name="{name}"]'
        elif src:
            where = f'iframe[src="{src[:120]}"]'
        else:
            where = f"iframe:nth-of-type({index})"
        locators.append(f"{where}（src={src[:120] if src else '（未声明）'}）")
    return tuple(locators)

