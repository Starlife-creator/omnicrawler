"""镜像预置与启用补丁的单元测试（决策七）。"""

from __future__ import annotations

from omnicrawler.services import mirror_presets as mp


class TestPresetMirrorGroup:
    def test_official_weight_is_highest(self) -> None:
        group = mp.preset_mirror_group()
        assert mp.CANONICAL_PYPI in group
        entries = group[mp.CANONICAL_PYPI]
        # 官方源权重必须最高（保证健康时置顶，决策一）
        official = next(e for e in entries if e["host"] == "pypi.org")
        assert all(official["weight"] >= e["weight"] for e in entries)

    def test_contains_official_and_domestic_mirrors(self) -> None:
        hosts = {e["host"] for e in mp.preset_mirror_group()[mp.CANONICAL_PYPI]}
        assert "pypi.org" in hosts
        assert any("tuna" in h for h in hosts)
        assert any("aliyun" in h for h in hosts)

    def test_weights_descending(self) -> None:
        entries = mp.preset_mirror_group()[mp.CANONICAL_PYPI]
        weights = [e["weight"] for e in entries]
        assert weights == sorted(weights, reverse=True)


class TestMirrorsEnabled:
    def test_absent_section_is_disabled(self) -> None:
        assert mp.mirrors_enabled({}) is False
        assert mp.mirrors_enabled({"mirrors": None}) is False

    def test_explicit_enabled(self) -> None:
        assert mp.mirrors_enabled({"mirrors": {"enabled": True}}) is True
        assert mp.mirrors_enabled({"mirrors": {"enabled": False}}) is False


class TestEnableMirrorsPatch:
    def test_patch_enables_and_adds_group(self) -> None:
        patch = mp.enable_mirrors_patch({})
        assert patch["mirrors"]["enabled"] is True
        assert mp.CANONICAL_PYPI in patch["mirrors"]["groups"]

    def test_patch_does_not_mutate_input(self) -> None:
        raw: dict = {"mirrors": {"enabled": False}}
        mp.enable_mirrors_patch(raw)
        assert raw["mirrors"]["enabled"] is False  # 原字典未被改动
        assert "groups" not in raw["mirrors"]

    def test_patch_preserves_existing_tuning(self) -> None:
        raw = {
            "mirrors": {
                "enabled": False,
                "failure_threshold": 9,
                "probe_timeout_seconds": 1.5,
                "groups": {"other.example": [{"host": "other.example"}]},
            }
        }
        patch = mp.enable_mirrors_patch(raw)
        mirrors = patch["mirrors"]
        assert mirrors["failure_threshold"] == 9          # 用户调优保留
        assert mirrors["probe_timeout_seconds"] == 1.5
        assert "other.example" in mirrors["groups"]       # 既有组保留
        assert mp.CANONICAL_PYPI in mirrors["groups"]     # PyPI 组补齐


class TestPresetIsConsumableByRegistry:
    """端到端：预置补丁产出的配置必须能被 MirrorRegistry 直接加载并排序。

    这是"决策七：预置候选清单 + 一键启用"真正接线到安装器的关键——
    补丁产出的结构必须与 ``MirrorRegistry.ordered_endpoints`` 的口径一致。
    """

    class _Cfg:
        def __init__(self, raw: dict) -> None:
            self._raw = raw

        def section(self, name: str) -> dict:
            value = self._raw.get(name, {})
            return value if isinstance(value, dict) else {}

    def test_registry_loads_patch_and_official_first(self) -> None:
        from omnicrawler.sources.mirror_registry import MirrorRegistry

        patch = mp.enable_mirrors_patch({})
        raw = {"mirrors": patch["mirrors"], "http": {"allow_private_network": False}}
        registry = MirrorRegistry(self._Cfg(raw))

        assert registry.enabled is True
        endpoints = registry.ordered_endpoints(mp.CANONICAL_PYPI)
        # 返回 (canonical, host)：host 才是用于构建 --index-url 的站点
        hosts = [host for _canonical, host in endpoints]
        # 全部预置站点都在，且官方源排在第一位（健康时恒定置顶，决策一）
        assert hosts[0] == "pypi.org"
        assert "mirrors.tuna.tsinghua.edu.cn" in hosts
        assert "mirrors.aliyun.com" in hosts
        # 官方源只出现一次（不因 canonical 补齐而重复）
        assert hosts.count("pypi.org") == 1

    def test_disabled_patch_never_loads(self) -> None:
        from omnicrawler.sources.mirror_registry import MirrorRegistry

        registry = MirrorRegistry(self._Cfg({}))
        assert registry.enabled is False
        assert registry.ordered_endpoints(mp.CANONICAL_PYPI) == []


class _Attempt:
    """测试用最小 attempt（只带谓词要读的两个字段）。"""

    def __init__(self, host: str, kind: str, ok: bool = False) -> None:
        self.host = host
        self.kind = kind
        self.ok = ok


class _Result:
    def __init__(self, ok: bool, attempts: list[_Attempt] | None = None) -> None:
        self.ok = ok
        self.attempts = attempts or []


class TestMirrorEndpointsFromPatch:
    def test_official_first_then_preset_order(self) -> None:
        endpoints = mp.mirror_endpoints_from_patch({})
        hosts = [host for _canonical, host in endpoints]
        # 官方源在首位（决策一），其余按预置清单顺序
        assert hosts[0] == "pypi.org"
        assert hosts[1] == "mirrors.tuna.tsinghua.edu.cn"
        assert all(canonical == mp.CANONICAL_PYPI for canonical, _host in endpoints)

    def test_matches_preset_group_hosts(self) -> None:
        patch = mp.enable_mirrors_patch({})
        expected = [e["host"] for e in patch["mirrors"]["groups"][mp.CANONICAL_PYPI]]
        assert [h for _c, h in mp.mirror_endpoints_from_patch({})] == expected


class TestShouldOfferMirrorAcceleration:
    def test_network_failure_on_official_offers(self) -> None:
        result = _Result(ok=False, attempts=[_Attempt("pypi.org", "network")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is True

    def test_timeout_retry_on_official_offers(self) -> None:
        result = _Result(ok=False, attempts=[_Attempt("files.pythonhosted.org", "timeout-retry")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is True

    def test_success_never_offers(self) -> None:
        result = _Result(ok=True, attempts=[_Attempt("pypi.org", "network")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is False

    def test_already_enabled_never_offers(self) -> None:
        result = _Result(ok=False, attempts=[_Attempt("pypi.org", "network")])
        raw = {"mirrors": {"enabled": True}}
        assert mp.should_offer_mirror_acceleration(result, config_raw=raw) is False

    def test_version_failure_does_not_offer(self) -> None:
        # 缺该版本换镜像无用 ⇒ 不该提示（避免误导用户）
        result = _Result(ok=False, attempts=[_Attempt("pypi.org", "version")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is False

    def test_conflict_failure_does_not_offer(self) -> None:
        result = _Result(ok=False, attempts=[_Attempt("pypi.org", "conflict")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is False

    def test_network_failure_on_mirror_only_does_not_offer(self) -> None:
        # 镜像源自己的网络失败：已启用镜像场景，再提示无意义
        result = _Result(ok=False, attempts=[_Attempt("mirrors.aliyun.com", "network")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is False

    def test_no_attempts_does_not_offer(self) -> None:
        result = _Result(ok=False, attempts=[])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is False

    def test_host_matching_is_case_insensitive(self) -> None:
        result = _Result(ok=False, attempts=[_Attempt("PyPI.ORG", "NETWORK")])
        assert mp.should_offer_mirror_acceleration(result, config_raw={}) is True
