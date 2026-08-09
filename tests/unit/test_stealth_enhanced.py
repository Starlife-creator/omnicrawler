"""Tests for fetching.stealth_enhanced — proxy rotation, fingerprint, human behavior."""

from __future__ import annotations

from unittest.mock import patch

from omnicrawl.fetching.stealth_enhanced import (
    Fingerprint,
    HumanBehavior,
    ProxyRotator,
    StealthEnhancer,
    StealthLevel,
    get_stealth_enhancer,
    random_fingerprint,
)

# ── StealthLevel ──────────────────────────────────────────────────────

class TestStealthLevel:
    def test_enum_values(self) -> None:
        assert StealthLevel.OFF.value == 0
        assert StealthLevel.LOW.value == 1
        assert StealthLevel.MEDIUM.value == 2
        assert StealthLevel.HIGH.value == 3

    def test_ordering(self) -> None:
        assert StealthLevel.OFF.value < StealthLevel.LOW.value < StealthLevel.MEDIUM.value < StealthLevel.HIGH.value


# ── Fingerprint ────────────────────────────────────────────────────────

class TestFingerprint:
    def test_defaults(self) -> None:
        fp = Fingerprint()
        assert fp.viewport_width == 1920
        assert fp.viewport_height == 1080
        assert fp.device_scale_factor == 1.0
        assert fp.is_mobile is False
        assert fp.has_touch is False
        assert fp.languages == ["zh-CN", "zh", "en"]
        assert fp.timezone == "Asia/Shanghai"
        assert fp.platform == "Win32"
        assert fp.hardware_concurrency == 8
        assert fp.device_memory == 8

    def test_custom_construction(self) -> None:
        fp = Fingerprint(
            user_agent="custom-ua",
            viewport_width=1024,
            viewport_height=768,
            timezone="UTC",
            platform="Linux",
            hardware_concurrency=4,
        )
        assert fp.user_agent == "custom-ua"
        assert fp.viewport_width == 1024
        assert fp.timezone == "UTC"


# ── ProxyRotator — add/remove ──────────────────────────────────────────

class TestProxyRotatorAddRemove:
    def test_add_proxy(self) -> None:
        pr = ProxyRotator()
        pr.add("http://p1:8080")
        assert pr.count == 1
        pr.add("http://p1:8080")  # duplicate ignored
        assert pr.count == 1

    def test_remove_proxy(self) -> None:
        pr = ProxyRotator(["http://p1:8080"])
        pr.remove("http://p1:8080")
        assert pr.count == 0

    def test_remove_clears_stats(self) -> None:
        pr = ProxyRotator(["http://p1:8080"])
        pr.report_failure("http://p1:8080")
        pr.remove("http://p1:8080")
        assert "http://p1:8080" not in pr._failures


# ── ProxyRotator — scoring ─────────────────────────────────────────────

class TestProxyRotatorScoring:
    def test_score_clean_proxy(self) -> None:
        pr = ProxyRotator(["http://p1:8080"])
        assert pr._score_proxy("http://p1:8080") > 50

    def test_score_failed_proxy(self) -> None:
        pr = ProxyRotator(["http://p1:8080"])
        pr.report_failure("http://p1:8080")
        pr.report_failure("http://p1:8080")
        score = pr._score_proxy("http://p1:8080")
        assert score < 30  # 100 - 2*40 = 20

    def test_score_minimum_floor(self) -> None:
        pr = ProxyRotator(["http://p1:8080"])
        for _ in range(10):
            pr.report_failure("http://p1:8080")
        assert pr._score_proxy("http://p1:8080") >= 1.0


# ── ProxyRotator — selection ───────────────────────────────────────────

class TestProxyRotatorSelection:
    def test_round_robin_empty(self) -> None:
        pr = ProxyRotator()
        assert pr.next_round_robin() is None

    def test_round_robin_cycles(self) -> None:
        pr = ProxyRotator(["http://p1", "http://p2", "http://p3"])
        p1 = pr.next_round_robin()
        p2 = pr.next_round_robin()
        p3 = pr.next_round_robin()
        p4 = pr.next_round_robin()
        assert [p1, p2, p3, p4] == ["http://p1", "http://p2", "http://p3", "http://p1"]

    def test_random_empty(self) -> None:
        pr = ProxyRotator()
        assert pr.next_random() is None

    def test_random_with_proxies(self) -> None:
        pr = ProxyRotator(["http://p1", "http://p2"])
        p = pr.next_random()
        assert p in ("http://p1", "http://p2")

    def test_random_all_failed_resets_and_picks(self) -> None:
        pr = ProxyRotator(["http://p1"])
        for _ in range(3):
            pr.report_failure("http://p1")
        p = pr.next_random()
        assert p == "http://p1"
        assert pr._failures["http://p1"] == 0  # reset

    def test_weighted_empty(self) -> None:
        pr = ProxyRotator()
        assert pr.next_weighted() is None

    def test_weighted_with_proxies(self) -> None:
        pr = ProxyRotator(["http://p1", "http://p2"])
        p = pr.next_weighted()
        assert p in ("http://p1", "http://p2")

    def test_domain_binding_cached(self) -> None:
        pr = ProxyRotator(["http://p1", "http://p2"])
        first = pr.next_for_domain("example.com")
        second = pr.next_for_domain("example.com")
        assert first == second  # same domain → same proxy

    def test_domain_binding_empty(self) -> None:
        pr = ProxyRotator()
        assert pr.next_for_domain("example.com") is None


# ── ProxyRotator — report ──────────────────────────────────────────────

class TestProxyRotatorReport:
    def test_report_failure(self) -> None:
        pr = ProxyRotator(["http://p1"])
        pr.report_failure("http://p1")
        assert pr._failures["http://p1"] == 1

    def test_report_success_reduces_failures(self) -> None:
        pr = ProxyRotator(["http://p1"])
        pr.report_failure("http://p1")
        pr.report_success("http://p1")
        assert pr._failures["http://p1"] == 0

    def test_report_success_with_latency(self) -> None:
        pr = ProxyRotator(["http://p1"])
        pr.report_success("http://p1", latency_ms=150.0)
        assert pr._latency["http://p1"] == 150.0

    def test_stats(self) -> None:
        pr = ProxyRotator(["http://p1", "http://p2"])
        pr.report_failure("http://p1")
        s = pr.stats
        assert s["total"] == 2
        assert s["failures"]["http://p1"] == 1


# ── HumanBehavior ──────────────────────────────────────────────────────

class TestHumanBehavior:
    def test_think_delay_range(self) -> None:
        """think_delay produces values within min/max translated to seconds."""
        delay = HumanBehavior.think_delay(200, 3000)
        assert 0.2 <= delay <= 3.0

    def test_type_delay_positive(self) -> None:
        delay = HumanBehavior.type_delay(100)
        assert delay > 0

    def test_scroll_pattern_small_page(self) -> None:
        """Small page (viewport >= height) returns empty list."""
        patterns = HumanBehavior.scroll_pattern(100, 200)
        assert patterns == []

    def test_scroll_pattern_large_page(self) -> None:
        """Scroll pattern produces valid (scroll, pause) tuples."""
        patterns = HumanBehavior.scroll_pattern(2000, 800)
        assert len(patterns) >= 1
        total = 0
        for scroll, pause in patterns:
            assert scroll > 0
            assert pause > 0
            total += scroll
        # 总滚动距离不应超过剩余可滚动区域
        assert total <= 2000 - 800

    def test_mouse_path_count(self) -> None:
        path = HumanBehavior.mouse_path(0, 0, 100, 100, steps=10)
        assert len(path) == 11  # steps + 1
        assert path[0] == (0, 0)
        assert path[-1] == (100, 100)


# ── StealthEnhancer — randomize ────────────────────────────────────────

class TestStealthEnhancerRandomize:
    def test_basic_randomize(self) -> None:
        se = StealthEnhancer(seed=42)
        fp = se.randomize()
        assert isinstance(fp, Fingerprint)
        assert fp.user_agent
        assert fp.viewport_width > 0
        assert fp.viewport_height > 0
        assert fp.languages
        assert fp.timezone
        assert fp.platform
        assert fp.webgl_vendor
        assert fp.webgl_renderer
        assert fp.accept_language
        assert fp.sec_ch_ua
        assert fp.sec_ch_ua_platform

    def test_deterministic_with_seed(self) -> None:
        se1 = StealthEnhancer(seed=42)
        se2 = StealthEnhancer(seed=42)
        fp1 = se1.randomize()
        fp2 = se2.randomize()
        assert fp1.user_agent == fp2.user_agent
        assert fp1.viewport_width == fp2.viewport_width

    def test_platform_inferred_from_ua(self) -> None:
        """Platform is inferred from User-Agent."""
        se = StealthEnhancer()
        # Run many times to cover all platform branches
        platforms_seen: set[str] = set()
        for _ in range(50):
            fp = se.randomize()
            platforms_seen.add(fp.platform)
        assert len(platforms_seen) >= 2  # At least two different platforms

    def test_generation_counter_increments(self) -> None:
        se = StealthEnhancer()
        assert se._generation == 0
        se.randomize()
        assert se._generation == 1

    def test_s2529_sec_ch_ua_version_matches_ua(self) -> None:
        se = StealthEnhancer(seed=7)
        for _ in range(30):
            fp = se.randomize()
            if "Chrome/" not in fp.user_agent:
                # 非 Chromium 内核 UA 不再注入 sec_ch_ua（避免自相矛盾）
                assert fp.sec_ch_ua == ""
                continue
            import re as _re

            version = _re.search(r"Chrome/(\d+)", fp.user_agent).group(1)
            assert f'Chromium";v="{version}"' in fp.sec_ch_ua
            assert f'Google Chrome";v="{version}"' in fp.sec_ch_ua

    def test_s2529_apply_to_context_does_not_create_blank_page(self) -> None:
        se = StealthEnhancer(level=StealthLevel.LOW)
        fp = se.randomize()
        created: list[str] = []
        page = type("Page", (), {"set_viewport_size": lambda self, _s: None})()

        class _Ctx:
            pages = [page]

            def new_page(self):
                created.append("new_page")

        se.apply_to_playwright_context(_Ctx(), fp, StealthLevel.LOW)
        assert created == []

    def test_s2529_timezone_offsets_cover_all_timezones(self) -> None:
        se = StealthEnhancer()
        fp = se.randomize()
        script = se._build_init_script(fp, StealthLevel.HIGH)
        for tz in ("Asia/Shanghai", "America/Chicago", "America/Los_Angeles", "Pacific/Auckland"):
            assert tz in script


# ── StealthEnhancer — proxy delegation ─────────────────────────────────

class TestStealthEnhancerProxy:
    def test_proxy_for_domain(self) -> None:
        se = StealthEnhancer(proxy_list=["http://p1:8080"])
        p = se.proxy_for_domain("example.com")
        assert p is not None

    def test_report_proxy_success(self) -> None:
        se = StealthEnhancer(proxy_list=["http://p1:8080"])
        se.report_proxy_result("http://p1:8080", success=True, latency_ms=50)
        # no crash, success reduces failures
        assert se.rotator._failures.get("http://p1:8080", 0) == 0

    def test_report_proxy_failure(self) -> None:
        se = StealthEnhancer(proxy_list=["http://p1:8080"])
        se.report_proxy_result("http://p1:8080", success=False)
        assert se.rotator._failures["http://p1:8080"] == 1


# ── StealthEnhancer — _build_init_script ───────────────────────────────

class TestBuildInitScript:
    def test_level_off_returns_empty(self) -> None:
        se = StealthEnhancer(level=StealthLevel.OFF)
        fp = se.randomize()
        script = se._build_init_script(fp, StealthLevel.OFF)
        assert script.strip() == ""

    def test_level_low_has_webdriver_override(self) -> None:
        se = StealthEnhancer()
        fp = se.randomize()
        script = se._build_init_script(fp, StealthLevel.LOW)
        assert "webdriver" in script
        assert "hardwareConcurrency" in script
        assert "screen" in script
        assert str(fp.viewport_width) in script
        # LOW should NOT have canvas/webgl noise
        assert "toDataURL" not in script

    def test_level_medium_has_canvas_noise(self) -> None:
        se = StealthEnhancer()
        fp = se.randomize()
        script = se._build_init_script(fp, StealthLevel.MEDIUM)
        assert "Canvas" in script
        assert "toDataURL" in script
        assert "WebGL" in script
        assert "plugins" in script

    def test_level_high_has_audio_context(self) -> None:
        se = StealthEnhancer()
        fp = se.randomize()
        script = se._build_init_script(fp, StealthLevel.HIGH)
        assert "AudioContext" in script
        assert "getTimezoneOffset" in script
        assert "getChannelData" in script


# ── StealthEnhancer — apply_to_playwright / selenium ───────────────────

class TestApplyToContext:
    def test_playwright_off_returns_early(self) -> None:
        se = StealthEnhancer(level=StealthLevel.OFF)
        fp = se.randomize()
        mock_context = type("Ctx", (), {"pages": [], "new_page": lambda: None})()
        # Should not raise, returns immediately for OFF
        se.apply_to_playwright_context(mock_context, fp, StealthLevel.OFF)

    def test_selenium_options(self) -> None:
        se = StealthEnhancer()
        fp = se.randomize()
        expected_ua = fp.user_agent

        class MockOptions:
            arguments: list[str] = []
            experimental_options: dict = {}

            def add_argument(self, arg: str) -> None:
                self.arguments.append(arg)

            def add_experimental_option(self, key: str, value: object) -> None:
                self.experimental_options[key] = value

        opts = MockOptions()
        StealthEnhancer.apply_to_selenium_options(opts, fp)

        assert any(expected_ua in a for a in opts.arguments)
        assert any("window-size" in a for a in opts.arguments)
        assert any("disable-blink-features=AutomationControlled" in a for a in opts.arguments)
        assert opts.experimental_options["excludeSwitches"] == ["enable-automation"]
        assert opts.experimental_options["useAutomationExtension"] is False


# ── Global convenience ─────────────────────────────────────────────────

class TestGlobalConvenience:
    def test_get_stealth_enhancer_singleton(self) -> None:
        se1 = get_stealth_enhancer()
        se2 = get_stealth_enhancer()
        assert se1 is se2

    def test_random_fingerprint(self) -> None:
        fp = random_fingerprint()
        assert isinstance(fp, Fingerprint)
        assert fp.user_agent


# ── CLI integration ────────────────────────────────────────────────────

class TestCLIMain:
    def test_main_no_crash(self) -> None:
        import sys

        from omnicrawl.fetching.stealth_enhanced import main
        with patch.object(sys, "argv", ["stealth_enhanced", "--count", "1"]):
            # --count 1 with default (text) mode — should not crash
            main()
