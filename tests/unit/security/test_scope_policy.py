"""站点范围（same_host）判定：apex ⇄ www 属于同一站点。

背景（2026-09-12 真实场景测试）：`scrapethissite.com/pages/simple/` 会 301 到
`www.scrapethissite.com/...`，而范围判定用**主机名字符串严格相等**比较，于是
`www.scrapethissite.com != scrapethissite.com` 被判成「超出种子站点」，
`_fetch.py` 抛 PermissionError，整轮 run 因「未交付任何页面」记为 failed——
用户看到的是"目标网站无数据"，实际是范围判据过严。

本文件锁定归一化后的三条性质：
1. apex 与 `www.` 互为同一站点（两个方向都放行）。
2. 其它子域仍被拦截（只归一化 `www.`，不放松到任意子域）。
3. 完全不同的域名仍被拦截。

范围判定与出网/DNS 是两件事：这里把 network policy 换成替身，只测站点范围逻辑。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.security.policy import WRITE_GUARD_REASON, ScopePolicy


class _AllowAllNetwork:
    """替身 network policy：范围单测不做 DNS（与 tests/unit/egress/ 同一惯例）。"""

    def __init__(self) -> None:
        self.calls = 0

    def allowed(self, url: str) -> tuple[bool, str]:
        self.calls += 1
        return True, ""


def _scope(tmp_path: Path, crawl: dict | None = None) -> ScopePolicy:
    raw = {
        "project": {"name": "scope", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "crawl": {**DEFAULTS["crawl"], **(crawl or {})},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com"},
        "egress": dict(DEFAULTS["egress"]),
    }
    policy = ScopePolicy(AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work"))
    stub = _AllowAllNetwork()
    policy._network = stub  # type: ignore[assignment] —— 只替换网络层，其余逻辑照跑
    assert policy._network is stub  # 断言替身确实生效（防止测试假通过）
    return policy


def test_apex_and_www_are_the_same_site(tmp_path: Path) -> None:
    policy = _scope(tmp_path, {"same_host": True})

    allowed, reason = policy.allowed(
        "https://www.example.com/pages/simple/", "https://example.com/pages/simple/"
    )
    assert allowed, reason

    # 反方向同样成立
    allowed, reason = policy.allowed(
        "https://example.com/pages/simple/", "https://www.example.com/pages/simple/"
    )
    assert allowed, reason


def test_other_subdomains_are_still_blocked(tmp_path: Path) -> None:
    policy = _scope(tmp_path, {"same_host": True})

    allowed, reason = policy.allowed("https://cdn.example.com/x", "https://example.com/")
    assert not allowed
    assert reason == "超出种子站点"


def test_unrelated_domains_are_still_blocked(tmp_path: Path) -> None:
    policy = _scope(tmp_path, {"same_host": True})

    allowed, reason = policy.allowed("https://example.net/x", "https://example.com/")
    assert not allowed
    assert reason == "超出种子站点"


def test_same_host_false_disables_scope_check(tmp_path: Path) -> None:
    policy = _scope(tmp_path, {"same_host": False})

    allowed, reason = policy.allowed("https://example.net/x", "https://example.com/")
    assert allowed, reason


# ── 内置写操作保护（走查 R1.4） ──────────────────────────────────────────
#
# 背景（0.13.0 实测）：某电商测试站的商品卡片带「加入购物车」链接，自动配置把它当普通
# 链接跟进 ⇒ 60 个响应里 25 个是 `?add-to-cart=NNN`，交付的 448 条里 400 条来自这些页面、
# 同一商品最多重复 16 次。GET 形态的写操作被爬虫触发就是对第三方的真实副作用。

_BLOCKED_WRITE_URLS = [
    "https://shop.example.com/shop/?add-to-cart=743",
    "https://shop.example.com/product/x?add_to_cart=12",
    "https://shop.example.com/cart",
    "https://shop.example.com/cart/",
    "https://shop.example.com/checkout",
    "https://shop.example.com/orders/991",
    "https://shop.example.com/logout",
    "https://shop.example.com/account/signout",
    "https://shop.example.com/items/delete",
    "https://shop.example.com/item?id=3&action=delete",
    "https://shop.example.com/wp-admin/",
    "https://shop.example.com/wp-login.php",
    "https://shop.example.com/list?unsubscribe=1",
]

_ALLOWED_READ_URLS = [
    "https://shop.example.com/",
    "https://shop.example.com/shop/page/2/",
    "https://shop.example.com/search?q=python",          # 检索是读操作，不得误拦
    "https://shop.example.com/?s=boston+bruins",          # WordPress 检索同样是读操作
    "https://shop.example.com/cartography/map",           # 只是词里含 "cart"，不是购物车路径
    "https://shop.example.com/order-of-the-phoenix",      # 页面标题含 order，不是下单动作
]


def test_write_operation_urls_are_blocked_by_default(tmp_path: Path) -> None:
    policy = _scope(tmp_path, {"same_host": True})

    for url in _BLOCKED_WRITE_URLS:
        allowed, reason = policy.allowed(url, "https://shop.example.com/")
        assert not allowed, f"写操作 URL 不应被跟随：{url}"
        assert reason == WRITE_GUARD_REASON, url


def test_read_only_urls_are_not_harmed(tmp_path: Path) -> None:
    """★ 误拦比漏拦更容易毁掉真实需求：检索、页码、含关键词的普通路径必须放行。"""
    policy = _scope(tmp_path, {"same_host": True})

    for url in _ALLOWED_READ_URLS:
        allowed, reason = policy.allowed(url, "https://shop.example.com/")
        assert allowed, f"读操作 URL 被误拦：{url}（{reason}）"


def test_allow_write_patterns_explicitly_permits(tmp_path: Path) -> None:
    policy = _scope(
        tmp_path,
        {"same_host": True, "allow_write_patterns": [r"[?&]add-to-cart="]},
    )

    allowed, reason = policy.allowed(
        "https://shop.example.com/shop/?add-to-cart=743", "https://shop.example.com/"
    )
    assert allowed, f"显式放行后应允许：{reason}"
    # 放行是**按模式**的，未被模式覆盖的写操作仍必须拦住
    allowed, reason = policy.allowed("https://shop.example.com/logout", "https://shop.example.com/")
    assert not allowed
    assert reason == WRITE_GUARD_REASON


def test_explicit_deny_patterns_still_win(tmp_path: Path) -> None:
    policy = _scope(
        tmp_path,
        {"same_host": True, "deny_patterns": [r"/blocked/"], "allow_write_patterns": [r"cart"]},
    )

    allowed, reason = policy.allowed("https://shop.example.com/blocked/x", "https://shop.example.com/")
    assert not allowed
    assert reason == "命中deny_patterns"

