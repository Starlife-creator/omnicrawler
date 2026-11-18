"""Tests for browser runtime specification resolution and slot multi-engine integration."""

from __future__ import annotations

import pytest

from omnicrawler.core.capability_slots import build_builtin_slot_registry
from omnicrawler.fetching.browser_launch import (
    _HEADLESS_FIDELITY_ARGS,
    _OCCLUSION_FEATURE,
    PLAYWRIGHT_CHROMIUM_CHANNEL,
    launch_kwargs,
    resolve_browser_runtime,
)


def test_default_browser_runtime_resolves_to_bundled_chromium() -> None:
    spec = resolve_browser_runtime({"browser": {"headless": True}, "http": {"verify_tls": True}})
    assert spec.engine_family == "chromium"
    assert spec.driver_kind == "playwright"
    assert spec.channel == PLAYWRIGHT_CHROMIUM_CHANNEL
    assert spec.executable_path is None
    assert spec.verify_tls is True
    assert spec.headless is True
    # Chromium in headless mode receives fidelity flags & occlusion feature
    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg in spec.launch_args
    assert any(_OCCLUSION_FEATURE in arg for arg in spec.launch_args)


def test_firefox_runtime_resolution_and_parameter_scoping() -> None:
    """Firefox 必须被分流到 firefox 引擎，且绝不接受 Chromium 专属的保真参数与 channel。"""
    spec = resolve_browser_runtime(
        {"browser": {"headless": True, "provider_id": "playwright-firefox"}, "http": {"verify_tls": True}}
    )
    assert spec.engine_family == "firefox"
    assert spec.driver_kind == "playwright"
    assert spec.channel is None
    # Firefox 不得包含 Chromium 专属的无头参数
    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg not in spec.launch_args
    assert not any(_OCCLUSION_FEATURE in arg for arg in spec.launch_args)

    kwargs = launch_kwargs(spec)
    assert "channel" not in kwargs
    assert kwargs["headless"] is True


def test_webkit_runtime_resolution() -> None:
    spec = resolve_browser_runtime(preferred_id="playwright-webkit")
    assert spec.engine_family == "webkit"
    assert spec.driver_kind == "playwright"
    assert spec.channel is None
    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg not in spec.launch_args


def test_selenium_engine_family_routing() -> None:
    chrome_spec = resolve_browser_runtime(preferred_id="selenium")
    assert chrome_spec.engine_family == "chromium"
    assert chrome_spec.driver_kind == "selenium"
    assert chrome_spec.channel is None

    firefox_spec = resolve_browser_runtime(preferred_id="selenium-firefox")
    assert firefox_spec.engine_family == "firefox"
    assert firefox_spec.driver_kind == "selenium"
    assert firefox_spec.channel is None


def test_custom_executable_path_clears_channel() -> None:
    spec = resolve_browser_runtime(
        {"browser": {"executable_path": "C:/Browsers/my-chrome.exe", "headless": False}}
    )
    assert spec.engine_family == "chromium"
    assert spec.executable_path == "C:/Browsers/my-chrome.exe"
    assert spec.channel is None
    assert spec.headless is False
    # headful 模式下不加保真参数
    for arg in _HEADLESS_FIDELITY_ARGS:
        assert arg not in spec.launch_args


def test_tls_verification_flag_mapping() -> None:
    # Chromium: verify_tls=False -> adds --ignore-certificate-errors
    cr_spec = resolve_browser_runtime({"http": {"verify_tls": False}})
    assert "--ignore-certificate-errors" in cr_spec.launch_args
    assert cr_spec.verify_tls is False

    # Firefox: verify_tls=False -> sets user prefs instead of raw chromium CLI flag
    ff_spec = resolve_browser_runtime(
        {"browser": {"provider_id": "playwright-firefox"}, "http": {"verify_tls": False}}
    )
    assert "--ignore-certificate-errors" not in ff_spec.launch_args
    assert ff_spec.firefox_user_prefs.get("webdriver_accept_untrusted_certs") is True
    assert ff_spec.verify_tls is False


def test_reject_explicit_ignore_certificate_errors_in_launch_args() -> None:
    """任何引擎试图在 launch_args 里直接声明 --ignore-certificate-errors 均被拒。"""
    with pytest.raises(ValueError, match="browser.launch_args 禁止关闭 TLS 校验"):
        resolve_browser_runtime({"browser": {"launch_args": ["--ignore-certificate-errors"]}})

    with pytest.raises(ValueError, match="browser.launch_args 禁止关闭 TLS 校验"):
        resolve_browser_runtime(
            {"browser": {"provider_id": "playwright-firefox", "launch_args": ["--ignore-certificate-errors"]}}
        )


def test_capability_slot_browser_providers_and_preference() -> None:
    registry = build_builtin_slot_registry()
    slot = registry.slot("browser")
    assert slot is not None

    ids = [p.provider_id for p in slot.providers()]
    assert "playwright" in ids
    assert "playwright-firefox" in ids
    assert "playwright-webkit" in ids
    assert "selenium" in ids
    assert "selenium-firefox" in ids
    assert "system-browser" in ids

    # 默认活跃提供者为优先级最高的 playwright (Chromium)
    active = slot.active()
    assert active is not None
    assert active.provider_id == "playwright"
    assert active.metadata.get("bundled") is True

    # 切换槽位首选项
    slot.set_preferred("playwright-firefox")
    assert slot.active().provider_id == "playwright-firefox"

    # resolve_browser_runtime 可直接消费包含 slot_registry 的解析
    spec = resolve_browser_runtime(slot_registry=registry)
    assert spec.engine_family == "firefox"
