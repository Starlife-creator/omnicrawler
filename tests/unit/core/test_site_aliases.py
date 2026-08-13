"""P2-5：域名 / 站点别名注册表单元测试。

覆盖：normalize_host、resolve / has_alias、环境隔离、传递性、循环检测、
反向查询 aliases_for、默认单例、metrics 消费方行为。
"""

from __future__ import annotations

import os

import pytest


class TestNormalizeHost:
    def test_casefold_and_dot(self) -> None:
        from omnicrawl.core.site_aliases import normalize_host

        assert normalize_host("EXAMPLE.COM.") == "example.com"
        assert normalize_host("  m.Example.COM  ") == "m.example.com"

    def test_empty(self) -> None:
        from omnicrawl.core.site_aliases import normalize_host

        assert normalize_host("") == ""
        assert normalize_host("   ") == ""
        assert normalize_host(None) == ""  # type: ignore[arg-type]


class TestSiteAliasRegistry:
    def test_basic_alias_resolves(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("m.example.com", "example.com")
        assert reg.resolve("m.example.com") == "example.com"
        assert reg.resolve("example.com") == "example.com"  # canonical 自稳定
        assert reg.resolve("unknown.example.com") == "unknown.example.com"

    def test_has_alias_matches(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("m.example.com", "example.com")
        assert reg.has_alias("m.example.com") is True
        assert reg.has_alias("example.com") is False
        assert reg.has_alias("") is False

    def test_environment_scoped_alias(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("preview.example.com", "example.com", environments={"preview", "dev"})
        # 未指定环境 → 不命中环境作用域别名
        assert reg.resolve("preview.example.com") == "preview.example.com"
        # 指定预览环境 → 归并
        assert reg.resolve("preview.example.com", environment="dev") == "example.com"
        assert reg.resolve("preview.example.com", environment="preview") == "example.com"
        # 指定 prod → 不生效
        assert reg.resolve("preview.example.com", environment="prod") == "preview.example.com"

    def test_default_environment_from_ctor_and_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        # 显式构造
        reg = SiteAliasRegistry(default_environment="dev")
        reg.add_alias("x.test", "x.prod", environments={"dev"})
        assert reg.resolve("x.test") == "x.prod"

        # 环境变量驱动（首次构造时读取）
        monkeypatch.setitem(os.environ, "OMNICRAWL_ENVIRONMENT", "staging")
        reg2 = SiteAliasRegistry()
        assert reg2.default_environment == "staging"
        reg2.add_alias("a.stg", "a.prod", environments={"staging"})
        assert reg2.resolve("a.stg") == "a.prod"
        # 可覆盖
        reg2.set_default_environment("prod")
        assert reg2.resolve("a.stg") == "a.stg"

    def test_transitive_resolve(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("b.example.com", "a.example.com")
        reg.add_alias("c.example.com", "b.example.com")
        # c → b → a
        assert reg.resolve("c.example.com") == "a.example.com"

    def test_self_alias_is_ignored(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("example.com", "example.com")
        assert not reg.has_alias("example.com")
        # 空输入
        reg.add_alias("", "canonical.com")
        reg.add_alias("alias.com", "")
        assert reg.resolve("alias.com") == "alias.com"

    def test_add_alias_automatically_collapses_transitive_canonicals(self) -> None:
        """add_alias 在写入时已把 canonical 归并为 terminal，因此 API 层面不会产生环。"""
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("a", "b")
        reg.add_alias("b", "c")
        # 尝试 c→a（潜在环路入口）：写入时内部 resolve(a)=c，结果会坍缩为自映射并被忽略
        # 即不抛异常，也不建立错误记录；最终 resolve(a) 仍然到 c
        reg.add_alias("c", "a")
        assert reg.resolve("a") == "c"
        assert reg.resolve("c") == "c"  # 自稳定
        # 没有多余别名
        assert not reg.has_alias("c")

    def test_cycle_detected_on_resolve_after_manual_tamper(self) -> None:
        """通过底层 dict 手工构造循环，验证 resolve 侧兜底也会抛。"""
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("a", "b")
        reg.add_alias("b", "c")
        # 手工注入（内部私有，仅为了验证 resolve 的防御分支）
        from omnicrawl.core.site_aliases import _AliasEntry

        reg._aliases["c"] = _AliasEntry("a")  # type: ignore[assignment]
        with pytest.raises(ValueError, match="循环"):
            reg.resolve("a")

    def test_aliases_for_reverse_query(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("m.example.com", "example.com")
        reg.add_alias("touch.example.com", "example.com")
        reg.add_alias("pre.example.com", "example.com", environments={"dev"})
        # 全局生效 + 指定环境
        assert reg.aliases_for("example.com") == ["m.example.com", "touch.example.com"]
        assert reg.aliases_for("example.com", environment="dev") == [
            "m.example.com",
            "pre.example.com",
            "touch.example.com",
        ]
        # 未知 canonical 返回空
        assert reg.aliases_for("not-there.com") == []
        # 传递性：c → b → a 下 aliases_for(a) 应同时包含 b 与 c
        reg2 = SiteAliasRegistry()
        reg2.add_alias("b", "a")
        reg2.add_alias("c", "b")
        assert reg2.aliases_for("a") == ["b", "c"]

    def test_clear_and_default_singleton_is_isolated(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg1 = SiteAliasRegistry()
        reg1.add_alias("a", "b")
        reg1.clear()
        assert not reg1.has_alias("a")
        # 单例不应该跟手动构造共享 state
        single = SiteAliasRegistry.default()
        # 保证独立：单例在首次访问时创建，手动构造清空后未向单例写
        assert single is not reg1

    def test_merge_env_sets_when_same_alias_registered_twice(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        reg = SiteAliasRegistry()
        reg.add_alias("mirror.example.com", "example.com", environments={"dev"})
        reg.add_alias("mirror.example.com", "example.com", environments={"staging"})
        # 合并后两环境都生效
        assert reg.resolve("mirror.example.com", environment="dev") == "example.com"
        assert reg.resolve("mirror.example.com", environment="staging") == "example.com"
        # 显式升级为全局：一次为空集合即把规则变全局
        reg.add_alias("mirror.example.com", "example.com")
        assert reg.resolve("mirror.example.com", environment="prod") == "example.com"


class TestDefaultRegistryLazy:
    def test_default_returns_same_instance(self) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry

        a = SiteAliasRegistry.default()
        b = SiteAliasRegistry.default()
        assert a is b


class TestMetricsConsumer:
    """证明 SiteAliasRegistry 已接入 metrics.record_fetch 的非孤儿消费点。"""

    @staticmethod
    def _make_result(final_url: str, *, body: bytes = b"ok", status: int = 200) -> object:
        """用临时类伪造最小 FetchResult 形（鸭子类型），避免构造 CrawlRequest。"""
        from dataclasses import dataclass

        @dataclass
        class Fake:
            final_url: str
            status: int
            body: bytes
            elapsed_seconds: float = 0.1
            headers: dict[str, str] = None  # type: ignore[assignment]

            def __post_init__(self) -> None:
                if self.headers is None:
                    self.headers = {}

        return Fake(final_url=final_url, status=status, body=body)

    def test_host_collapses_to_canonical_in_metrics(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from omnicrawl.core.site_aliases import SiteAliasRegistry
        from omnicrawl.services.metrics import RunMetrics

        reg = SiteAliasRegistry()
        reg.add_alias("m.shop.example.com", "shop.example.com")
        # 用 monkeypatch 替换掉 default() 返回的单例 → 返回我们自己的 reg，
        # 避免污染进程级单例状态。
        monkeypatch.setattr(SiteAliasRegistry, "default", staticmethod(lambda: reg))

        metrics = RunMetrics()
        r1 = self._make_result("https://m.shop.example.com/item/1", body=b"ok")
        r2 = self._make_result("https://shop.example.com/item/2", body=b"ok2")
        metrics.record_fetch(r1, engine="httpx")  # type: ignore[arg-type]
        metrics.record_fetch(r2, engine="httpx")  # type: ignore[arg-type]

        # 两条请求被汇总到同一个 canonical host 标签
        snapshot = metrics.snapshot()
        by_host: dict[str, int] = {}
        for entry in snapshot["counters"]:
            if entry["name"] != "omnicrawl_requests_total":
                continue
            host = entry["labels"].get("host", "")
            by_host[host] = by_host.get(host, 0) + entry["value"]
        # canonical host 的总量 = 2；m.* 独立 host 不再出现
        assert by_host.get("shop.example.com", 0) == 2
        assert "m.shop.example.com" not in by_host

    def test_resolve_exception_does_not_break_record_fetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """解析异常不会冒泡到抓取主流程（防御性编程）。"""
        from omnicrawl.services.metrics import RunMetrics

        class Evil:
            @staticmethod
            def default():
                class R:
                    @staticmethod
                    def resolve(_host):
                        raise RuntimeError("boom")

                return R()

        monkeypatch.setattr("omnicrawl.services.metrics.SiteAliasRegistry", Evil)
        metrics = RunMetrics()
        r = self._make_result("https://a.example.com/", body=b"x")
        # 不抛
        metrics.record_fetch(r, engine="httpx")  # type: ignore[arg-type]
        snapshot = metrics.snapshot()
        # 回退到 raw host（计数器至少有数据）
        assert snapshot["counters"]
