"""B02-027：source.kind 白名单单一真源回归测试。

验证 GUI 校验白名单与内核注册共用 ``sources.sources.SUPPORTED_SOURCE_KINDS``，
4 个内置 ``site_*`` 适配器不再被 GUI 误拒，且白名单不再有手抄副本。
"""

from __future__ import annotations

from omnicrawl.gui.core.validator import VALID_SOURCE_KINDS
from omnicrawl.sources.sources import SITE_ADAPTER_KINDS, SUPPORTED_SOURCE_KINDS


def test_gui_whitelist_is_single_source_of_truth() -> None:
    """GUI 白名单直接引用内核共享常量，不再手抄。"""
    assert set(VALID_SOURCE_KINDS) == set(SUPPORTED_SOURCE_KINDS)


def test_site_adapters_in_whitelist() -> None:
    """4 个内置站点适配器必须在白名单内（此前被 GUI 误拒）。"""
    for kind in SITE_ADAPTER_KINDS:
        assert kind in VALID_SOURCE_KINDS
    assert {"site_wordpress", "site_drupal", "site_mediawiki", "site_discourse"} <= set(
        VALID_SOURCE_KINDS
    )


def test_validate_full_config_accepts_site_adapter_kind(tmp_path) -> None:
    """GUI 校验器接受 site_* 适配器 kind（回归：曾被误拒）。"""
    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.core.validator import validate_full_config

    cfg = CrawlConfig(source_kind="site_wordpress", seed_urls=["https://example.org/"])
    errors, _warnings = validate_full_config(cfg)
    assert not [e for e in errors if "网站类型" in e or "source.kind" in e]


def test_unknown_kind_still_rejected(tmp_path) -> None:
    """未知 kind 仍被拒（门禁没有放松）。"""
    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.core.validator import validate_full_config

    cfg = CrawlConfig(source_kind="site_unknown", seed_urls=["https://example.org/"])
    errors, _warnings = validate_full_config(cfg)
    assert any("网站类型" in e or "source.kind" in e for e in errors)


def test_extra_source_kinds_allows_plugin_registered_kind(tmp_path) -> None:
    """D10-b：extra_source_kinds 传入插件注册源类型后不再误拒。"""
    from omnicrawl.gui.core.config_model import CrawlConfig
    from omnicrawl.gui.core.validator import validate_full_config

    cfg = CrawlConfig(source_kind="my_plugin_source", seed_urls=["https://example.org/"])
    # 未传入 extra → 拒绝
    errors_no, _w = validate_full_config(cfg)
    assert any("网站类型" in e for e in errors_no)
    # 传入 extra（模拟插件注册源）→ 放行
    errors_yes, _w = validate_full_config(cfg, extra_source_kinds={"my_plugin_source"})
    assert not [e for e in errors_yes if "网站类型" in e]


def test_plugin_source_kinds_returns_registered_kinds(tmp_path) -> None:
    """D10-b：plugin_source_kinds 从项目根构建 registry 提取已注册源类型。"""
    from omnicrawl.gui.core.validator import plugin_source_kinds
    from omnicrawl.sources.sources import SUPPORTED_SOURCE_KINDS

    kinds = plugin_source_kinds(str(tmp_path))
    assert isinstance(kinds, set)
    # 至少包含全部内置通用类型
    assert set(SUPPORTED_SOURCE_KINDS) <= kinds


def test_sources_register_uses_generic_kinds_only(tmp_path) -> None:
    """sources.register 只注册通用 kind；site_* 由 site_adapters 注册专用类。"""
    from omnicrawl.plugins.plugins import Registry
    from omnicrawl.sources import site_adapters, sources

    registry = Registry()
    sources.register(registry)
    assert not set(SITE_ADAPTER_KINDS) & set(registry.sources)
    site_adapters.register(registry)
    for kind in SITE_ADAPTER_KINDS:
        assert kind in registry.sources
