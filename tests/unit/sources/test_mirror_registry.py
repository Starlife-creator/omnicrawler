"""P3-1：Mirror Registry 单元测试（组加载、健康 EWMA、URL 改写、失败摘流）。"""

from __future__ import annotations


class FakeAppConfig:
    def __init__(self, section_dict: dict) -> None:
        self._d = section_dict

    def section(self, name: str) -> dict:
        return self._d.get(name, {})


class TestMirrorRegistryBasics:
    def test_not_enabled_is_empty(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({"mirrors": {"enabled": False}})
        reg = MirrorRegistry(cfg)
        assert reg.enabled is False
        assert reg.group_count == 0
        rewritten, canonical = reg.rewrite_url("https://example.com/x")
        assert rewritten == "https://example.com/x"
        assert canonical is None

    def test_enabled_loads_groups_preserves_canonical(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({
            "mirrors": {
                "enabled": True,
                "groups": {
                    "pypi.org": [
                        {"host": "pypi.org", "weight": 1.0},
                        {"host": "mirrors.tuna.tsinghua.edu.cn", "weight": 2.0},
                    ]
                },
            }
        })
        reg = MirrorRegistry(cfg)
        assert reg.enabled is True
        assert reg.group_count == 1
        # resolve 能选到最高权重的 tuna
        canonical, picked = reg.resolve_host("pypi.org")
        assert canonical == "pypi.org"
        assert picked == "mirrors.tuna.tsinghua.edu.cn"

    def test_unknown_host_passthrough(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({"mirrors": {"enabled": True, "groups": {"pypi.org": [{"host": "pypi.org"}]}}})
        reg = MirrorRegistry(cfg)
        rewritten, canonical = reg.rewrite_url("https://others.com/x")
        assert rewritten == "https://others.com/x"
        assert canonical is None


class TestHealthAndFailover:
    def test_failure_threshold_kicks_and_success_restores(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({
            "mirrors": {
                "enabled": True,
                "failure_threshold": 2,
                "success_threshold": 2,
                "groups": {
                    "pypi.org": [
                        {"host": "pypi.org", "weight": 1},
                        {"host": "mirror-a.com", "weight": 5},  # 默认第一选择
                    ]
                },
            }
        })
        reg = MirrorRegistry(cfg)
        # 1. 初始：pick = mirror-a（权重高）
        _, picked = reg.resolve_host("pypi.org")
        assert picked == "mirror-a.com"
        # 2. mirror-a 连续失败 2 次 → 应被摘流，之后 pick = pypi.org
        reg.record_failure("pypi.org", "mirror-a.com")
        reg.record_failure("pypi.org", "mirror-a.com")
        _, picked2 = reg.resolve_host("pypi.org")
        assert picked2 == "pypi.org"
        # 3. endpoint_status 能反映 unhealthy
        status = reg.endpoint_status("pypi.org")
        assert status is not None
        a_stat = next(s for s in status if s["host"] == "mirror-a.com")
        assert a_stat["healthy"] is False
        assert a_stat["consecutive_failures"] >= 2
        # 4. mirror-a 恢复（成功 2 次）→ 再 pick 它
        reg.record_success("pypi.org", "mirror-a.com")
        reg.record_success("pypi.org", "mirror-a.com")
        _, picked3 = reg.resolve_host("pypi.org")
        assert picked3 == "mirror-a.com"


class TestUrlRewrite:
    def test_rewrite_preserves_path_port_and_query(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({
            "mirrors": {
                "enabled": True,
                "groups": {
                    "orig.example.com": [
                        {"host": "orig.example.com", "weight": 1},
                        {"host": "cdn.example.com", "weight": 10},
                    ]
                },
            }
        })
        reg = MirrorRegistry(cfg)
        rewritten, canonical = reg.rewrite_url(
            "https://orig.example.com:8443/api/v1/list?q=hello&x=1#top"
        )
        assert canonical == "orig.example.com"
        # cdn 被选
        assert rewritten.startswith("https://cdn.example.com:8443/")
        # path/query/fragment 保留
        assert rewritten.endswith("/api/v1/list?q=hello&x=1#top")

    def test_rewrite_same_host_no_op(self) -> None:
        from omnicrawl.sources.mirror_registry import MirrorRegistry

        cfg = FakeAppConfig({
            "mirrors": {
                "enabled": True,
                "groups": {
                    "a.test": [{"host": "a.test", "weight": 1}]
                },
            }
        })
        reg = MirrorRegistry(cfg)
        # 只有 a.test 一个端点，pick 它，但 rewritten 不变
        rewritten, canonical = reg.rewrite_url("https://a.test/foo")
        assert rewritten == "https://a.test/foo"
        assert canonical == "a.test"


class TestModelsWithCopy:
    def test_crawl_request_with_url_and_meta(self) -> None:
        from omnicrawl.core.models import CrawlRequest

        r = CrawlRequest(url="https://orig/")
        fp_before = r.fingerprint
        r2 = r.with_url("https://new/")
        assert r2.url == "https://new/"
        # 指纹失效（不同 URL → 不同指纹）
        assert r2.fingerprint != fp_before
        # with_meta_update: 原 request 不变
        r3 = r.with_meta_update({"foo": 1})
        assert r.meta == {}
        assert r3.meta == {"foo": 1}
        # 同 URL 不构造新对象
        assert r.with_url("https://orig/") is r

    def test_fetch_result_with_final_and_meta(self) -> None:
        from omnicrawl.core.models import CrawlRequest, FetchResult

        req = CrawlRequest(url="https://x/")
        fr = FetchResult(req, "https://x/final", 200, {}, b"ok", 0.01)
        fr2 = fr.with_final_url("https://canonical/final")
        assert fr.final_url == "https://x/final"  # 原不变
        assert fr2.final_url == "https://canonical/final"
        fr3 = fr2.with_meta_update({"mirror_used": "cdn"})
        assert fr3.meta["mirror_used"] == "cdn"
        # 相同 final 不必 replace
        assert fr2.with_final_url("https://canonical/final") is fr2
        assert fr2.with_meta_update({}) is fr2
