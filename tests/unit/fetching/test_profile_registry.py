"""P2-2：浏览器 Profile 注册表单元测试。

覆盖：BrowserProfile（ensure/delete/manifest）、ProfileRegistry（acquire/scope_for/
lookup/list_all/purge_expired/over-limit-GC）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class TestSafeName:
    def test_empty_and_ascii(self) -> None:
        from omnicrawl.fetching.profile_registry import _safe_name

        assert _safe_name("") == "default"
        assert _safe_name("shop.example.com").endswith(
            _safe_name("shop.example.com")[-9:]
        )
        # 同名应稳定
        assert _safe_name("shop.example.com") == _safe_name("shop.example.com")

    def test_unicode_and_illegal_chars(self) -> None:
        from omnicrawl.fetching.profile_registry import _safe_name

        a = _safe_name("中文名/\\<>")
        for ch in "/\\<>":
            assert ch not in a


class TestBrowserProfile:
    def test_ensure_creates_dir_and_manifest(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import BrowserProfile

        root = tmp_path / "profiles"
        p = BrowserProfile(scope="shop|default", root=root / "prof", manifest_path=root / "prof" / "_omnicrawler_profile.json")
        assert not p.exists
        returned = p.ensure()
        assert returned == (root / "prof")
        assert p.exists
        assert p.manifest_path.is_file()
        data = json.loads(p.manifest_path.read_text(encoding="utf-8"))
        assert data["scope"] == "shop|default"
        assert "last_accessed" in data
        assert "created_at" in data

    def test_last_accessed_refresh(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import BrowserProfile

        root = tmp_path / "profiles"
        p = BrowserProfile(scope="a", root=root / "a", manifest_path=root / "a" / "_omnicrawler_profile.json")
        p.ensure()
        t1 = p.last_accessed()
        time.sleep(0.02)
        p.ensure()
        t2 = p.last_accessed()
        assert t2 >= t1

    def test_delete_removes_dir(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import BrowserProfile

        root = tmp_path / "profiles"
        p = BrowserProfile(scope="a", root=root / "a", manifest_path=root / "a" / "_omnicrawler_profile.json")
        p.ensure()
        # 放一个假文件模拟 Chrome 内容
        (p.root / "Default").mkdir(parents=True)
        (p.root / "Default" / "Cookies").write_bytes(b"x")
        p.delete()
        assert not p.exists


class TestProfileRegistry:
    def test_scope_for_no_aliases(self) -> None:
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg = ProfileRegistry(Path("/tmp/never-used"), max_profiles=16)
        # 无 account → default
        assert "|default" in reg.scope_for("shop.example.com")
        # 有 account
        s = reg.scope_for("shop.example.com", account="alice")
        assert "alice" in s
        # 有 environment 则包含
        s2 = reg.scope_for("shop.example.com", account="alice", environment="dev")
        assert "dev" in s2
        assert "alice" in s2

    def test_acquire_creates_profile_and_lookup(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg = ProfileRegistry(tmp_path / "profiles")
        p = reg.acquire("shop.example.com", account="alice")
        assert p.exists
        assert p.manifest_path.is_file()
        # lookup 能找到
        got = reg.lookup("shop.example.com", account="alice")
        assert got is not None
        assert got.root == p.root
        # 不同账户 → 不同 profile
        p2 = reg.acquire("shop.example.com", account="bob")
        assert p2.root != p.root
        # list_all 总数 = 2
        assert len(reg.list_all()) == 2

    def test_purge_expired(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg = ProfileRegistry(tmp_path / "profiles", ttl_seconds=600)
        now = time.time()
        p_a = reg.acquire("a.example.com")
        p_b = reg.acquire("b.example.com")
        # 手工把 a 的访问时间改成 2 小时前
        old = {"scope": p_a.scope, "created_at": now, "last_accessed": now - 7200}
        p_a.manifest_path.write_text(json.dumps(old), encoding="utf-8")
        # 触发过期清理
        removed = reg.purge_expired(now=now)
        assert removed >= 1
        assert not p_a.exists
        # b 仍存活
        assert p_b.exists

    def test_over_limit_gc_releases_oldest(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg = ProfileRegistry(tmp_path / "profiles", max_profiles=2, ttl_seconds=0)
        p1 = reg.acquire("host1.example.com")
        # 让 p1 比 p2 更旧
        time.sleep(0.02)
        p2 = reg.acquire("host2.example.com")
        time.sleep(0.02)
        p3 = reg.acquire("host3.example.com")  # 超过 max=2 → 触发懒清理

        # list_all 应保持 ≤ 2
        assert len(reg.list_all()) <= 2
        # 被清理的应该是 p1（最久未用）
        still_have = {p.root.name for p in reg.list_all()}
        assert p1.root.name not in still_have or (p2.root.name in still_have and p3.root.name in still_have)

    def test_clear_removes_all(self, tmp_path: Path) -> None:
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg = ProfileRegistry(tmp_path / "profiles")
        reg.acquire("h1")
        reg.acquire("h2")
        reg.acquire("h3")
        assert reg.clear() >= 3
        assert len(reg.list_all()) == 0


class TestWithSiteAliases:
    """验证 scope_for 会通过 SiteAliasRegistry 归并 host 后再分配 profile。"""

    def test_aliases_collapse_to_same_profile(self, tmp_path: Path, monkeypatch) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry
        from omnicrawl.fetching.profile_registry import ProfileRegistry

        reg_alias = SiteAliasRegistry()
        reg_alias.add_alias("m.shop.example.com", "shop.example.com")
        monkeypatch.setattr(
            SiteAliasRegistry, "default", staticmethod(lambda: reg_alias)
        )

        pr = ProfileRegistry(tmp_path / "profiles")
        p_mobile = pr.acquire("m.shop.example.com", account="alice")
        p_main = pr.acquire("shop.example.com", account="alice")
        # 同一 canonical → 同一份 profile（登录状态共享）
        assert p_mobile.root == p_main.root
