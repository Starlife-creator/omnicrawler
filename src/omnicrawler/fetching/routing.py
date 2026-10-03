from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.models import FetchResult

# S2.5.10：挑战页特征词收窄——强特征（页面任意前缀命中即判），
# 弱特征（access denied / captcha 等易在普通正文出现）仅当位于
# 可见文本（剥离 script/style 后）才判，避免脚本内特征词误判。
_STRONG_CHALLENGE_MARKERS = (
    b"cf-chl-", b"cloudflare ray id", b"just a moment",
    b"checking your browser", b"enable javascript and cookies",
)
_WEAK_CHALLENGE_MARKERS = (b"access denied", b"captcha")

# S2.5.10：SPA 根节点——不再要求空根（立即闭合），容器含子元素同样命中
_APP_ROOT_RE = re.compile(
    r"<(?:div|main)\b[^>]*\bid=[\"'](?:app|root|__next|__nuxt)[\"']",
    flags=re.I,
)


@dataclass(frozen=True, slots=True)
class BrowserDecision:
    escalate: bool
    code: str
    reason: str = ""


def browser_decision(result: FetchResult) -> BrowserDecision:
    """Bounded reason codes; page contents are never included in diagnostics."""
    if result.request.kind == "asset" or result.content_type not in {"", "text/html", "application/xhtml+xml"}:
        return BrowserDecision(False, "non_html_or_asset")
    # B03-012：挑战标记只扫前 200KB（响应体开头）——已知权衡：绝大多数验证页
    # 在头部即注入挑战脚本，扫全量在大响应上徒增开销；极端情况（挑战脚本后置）
    # 会漏判，可接受。
    prefix = result.body[:200_000].lower()
    if any(marker in prefix for marker in _STRONG_CHALLENGE_MARKERS):
        return BrowserDecision(True, "challenge_signature", "检测到验证页或访问挑战")
    text = prefix.decode("utf-8", errors="ignore")
    # 弱特征词只在可见文本内匹配（脚本/样式内出现不算）
    visible_text = re.sub(
        r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S
    ).lower()
    visible_text = re.sub(r"<!--.*?-->", " ", visible_text, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", visible_text)
    visible = re.sub(r"\s+", " ", visible).strip()
    if (
        any(marker.decode() in visible for marker in _WEAK_CHALLENGE_MARKERS)
        and (len(visible) < 500 or result.status in {403, 429, 503})
    ):
        return BrowserDecision(True, "challenge_visible", "检测到验证页或访问挑战")
    scripts = len(re.findall(r"<script\b", text, flags=re.I))
    app_roots = _APP_ROOT_RE.search(text) is not None
    if len(visible) < 80 and scripts >= 3 and app_roots:
        return BrowserDecision(True, "javascript_shell", "页面主要内容需要 JavaScript 渲染")
    return BrowserDecision(False, "static_content")


def needs_browser(result: FetchResult) -> tuple[bool, str]:
    """Compatibility API for page probes and extractors."""
    decision = browser_decision(result)
    return decision.escalate, decision.reason
