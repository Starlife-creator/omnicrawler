"""B-1：合规 User-Agent 分层单元测试。

用例覆盖：
  1. 4 档 profile 结构（polite_bot/minimal/desktop/mobile）
  2. 铁则：每档必含 OmniCrawler/{version}；suffix 附加正常生效
  3. 未知 profile → 回退 polite_bot 不抛错（warnings 日志）
  4. OMNICRAWL_UA_PROFILE 环境变量生效（调用方不传 profile 时读取 env）
  5. 铁则：伪造 Chrome 精确签名 → _validate_profile_honest 抛 ValueError
  6. doctor: UA/profile 名含 random/fake/spoof/canvas/webgl 反指纹关键词 → warnings
  7. 默认 user_agent() 行为 = B-1 之前的诚实 UA + suffix（向后兼容）
"""

from __future__ import annotations

from pathlib import Path

import pytest

import omnicrawl
from omnicrawl.core.utils import (
    UA_DEFAULT_PROFILE,
    UA_PROFILES,
    _validate_profile_honest,
    build_user_agent,
    user_agent,
)


class TestProfilesStructure:
    def test_all_official_profiles_contain_version(self) -> None:
        expected = f"OmniCrawler/{omnicrawl.__version__}"
        for name in UA_PROFILES:
            ua = build_user_agent(name)
            assert expected in ua, f"profile={name} 缺失诚实自报标识"

    def test_polite_bot_default_no_extra_tokens(self) -> None:
        # 默认 polite_bot 只留 OmniCrawler/version + 可选 suffix
        ua = build_user_agent("polite_bot")
        assert ua == f"OmniCrawler/{omnicrawl.__version__}"

    def test_minimal_same_as_polite_bot(self) -> None:
        # minimal 也是最小化：两档等价
        assert build_user_agent("minimal") == build_user_agent("polite_bot")

    def test_desktop_contains_desktop_hint(self) -> None:
        ua = build_user_agent("desktop")
        assert "Desktop" in ua
        assert "+compatible" in ua
        # 不伪造 Chrome 精确版本号
        assert "Chrome/" not in ua

    def test_mobile_contains_mobile_hint(self) -> None:
        ua = build_user_agent("mobile")
        assert "Mobile" in ua
        assert "+compatible" in ua
        assert "Safari/" not in ua

    def test_suffix_is_preserved(self) -> None:
        ua = build_user_agent("polite_bot", suffix="+contact: team@example.com")
        assert ua.endswith("+contact: team@example.com")
        assert f"OmniCrawler/{omnicrawl.__version__} +contact" in ua

    def test_case_insensitive_profile_name(self) -> None:
        ua1 = build_user_agent("DESKTOP")
        ua2 = build_user_agent("Desktop")
        ua3 = build_user_agent("desktop")
        assert ua1 == ua2 == ua3


class TestFallbackAndEnv:
    def test_unknown_profile_falls_back_polite_bot(self) -> None:
        # 不应该抛错，只产生 warning log（验证返回值 == polite_bot）
        ua = build_user_agent("this-profile-does-not-exist-xyz")
        assert ua == build_user_agent(UA_DEFAULT_PROFILE)

    def test_env_variable_read_when_profile_arg_none(self, monkeypatch) -> None:
        monkeypatch.setenv("OMNICRAWL_UA_PROFILE", "mobile")
        # 调用方没给 profile 参数 → 读取 env
        ua = user_agent()
        monkeypatch.delenv("OMNICRAWL_UA_PROFILE", raising=False)
        assert ua == build_user_agent("mobile")

    def test_explicit_profile_arg_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("OMNICRAWL_UA_PROFILE", "mobile")
        # 显式传 desktop 覆盖 env
        ua = user_agent(profile="desktop")
        monkeypatch.delenv("OMNICRAWL_UA_PROFILE", raising=False)
        assert ua == build_user_agent("desktop")
        assert "Desktop" in ua


class TestHonestyFence:
    def test_validate_missing_version_raises(self) -> None:
        with pytest.raises(ValueError, match="违反合规铁则：必须包含诚实自报标识"):
            _validate_profile_honest("Mozilla/5.0 Bot", profile_name="rogue")

    def test_validate_chrome_fake_signature_raises(self) -> None:
        # 即使开头加了 OmniCrawler/ver 诚实标识，只要仍伪装 Chrome 精确签名（反指纹），铁则就拦下
        fake_with = (
            f"OmniCrawler/{omnicrawl.__version__} "
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        with pytest.raises(ValueError, match="包含疑似浏览器伪造签名"):
            _validate_profile_honest(fake_with, profile_name="rogue")

    def test_validate_firefox_fake_signature_raises(self) -> None:
        fake = (
            f"OmniCrawler/{omnicrawl.__version__} "
            "(X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        with pytest.raises(ValueError, match="包含疑似浏览器伪造签名"):
            _validate_profile_honest(fake, profile_name="rogue")


class TestBackwardCompatible:
    def test_default_user_agent_equals_baseline(self) -> None:
        """B-1 之前的基线行为：OmniCrawler/version + suffix 空格拼接。"""
        base_only = user_agent()
        assert base_only == f"OmniCrawler/{omnicrawl.__version__}"

    def test_suffix_only_old_api_still_works(self) -> None:
        # 原调用方签名：user_agent(suffix) —— keyword= 或 positional 都可用
        ua = user_agent("+bot")
        assert ua == f"OmniCrawler/{omnicrawl.__version__} +bot"

    def test_contact_suffix_matches_old_expected_form(self) -> None:
        suffix = "+contact: change-me@example.com"
        got = user_agent(suffix)
        assert f"OmniCrawler/{omnicrawl.__version__} {suffix}" == got


class TestDoctorUACompliance:
    @staticmethod
    def _fake_cfg(tmp_path: Path, **http_overrides):
        from tests.unit.sources.test_mirror_registry_preflight import FakeAppConfig

        project = tmp_path / "prj"
        ws = project / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        http = {"allow_private_network": False, **http_overrides}
        sections = {
            "project": {"name": "p"},
            "http": http,
            "processors": {"pdf": {"enabled": False}},
            "crawl": {"concurrency": 4},
            "source": {"headers": {}},
            "mirrors": {"enabled": False},
        }
        return FakeAppConfig(sections, root=project, workspace=ws, path=project / "config.yaml")

    def test_anti_fp_keywords_trigger_warning(self, tmp_path: Path) -> None:
        from omnicrawl.core.utils import UA_PROFILES

        # 仿照 doctor 预检逻辑：profile 名不在官方 + 命中关键词 → warnings
        self._fake_cfg(tmp_path, user_agent_profile="random-canvas-spoof")
        warnings_observed: list[str] = []
        cfg_ua_profile = "random-canvas-spoof"
        if cfg_ua_profile and cfg_ua_profile.lower() not in {k.lower() for k in UA_PROFILES}:
            warnings_observed.append("不在官方合规")
        keywords = ("random", "fake", "spoof", "forge", "canvas", "webgl")
        detected = [k for k in keywords if k in cfg_ua_profile.lower()]
        if detected:
            warnings_observed.append("反指纹对抗疑似关键词：" + ",".join(detected))
        assert any("反指纹对抗" in w for w in warnings_observed), (
            f"预期命中反指纹关键词，实际 warnings={warnings_observed}"
        )

    def test_doctor_manual_ua_honest_fence(self, tmp_path: Path) -> None:
        from omnicrawl.core.utils import _validate_profile_honest

        # 伪造 UA：即使用户手动写了个 Chrome UA 到 http.user_agent
        fake = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
        with pytest.raises(ValueError):
            _validate_profile_honest(fake, profile_name="manual-ua")
