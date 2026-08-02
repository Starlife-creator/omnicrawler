from __future__ import annotations

import re

from ..core.models import FetchResult

CHALLENGE_MARKERS = (
    b"cf-chl-", b"cloudflare ray id", b"just a moment", b"checking your browser",
    b"enable javascript and cookies", b"access denied", b"captcha",
)


def needs_browser(result: FetchResult) -> tuple[bool, str]:
    """Conservative HTTP-to-browser escalation decision."""
    if result.request.kind == "asset" or result.content_type not in {"", "text/html", "application/xhtml+xml"}:
        return False, ""
    prefix = result.body[:200_000].lower()
    if any(marker in prefix for marker in CHALLENGE_MARKERS):
        return True, "检测到验证页或访问挑战"
    text = prefix.decode("utf-8", errors="ignore")
    visible = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<[^>]+>", " ", text, flags=re.I | re.S)
    visible = re.sub(r"\s+", " ", visible).strip()
    scripts = len(re.findall(r"<script\b", text, flags=re.I))
    app_roots = bool(re.search(r'<(?:div|main)\b[^>]+id=["\'](?:app|root|__next|__nuxt)["\'][^>]*>\s*</', text, flags=re.I))
    if len(visible) < 80 and scripts >= 3 and app_roots:
        return True, "页面主要内容需要 JavaScript 渲染"
    return False, ""
