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
from omnicrawler.security.policy import ScopePolicy


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
