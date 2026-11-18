"""多内核启动参数隔离：切到 Firefox/WebKit 时绝不混入 Chromium 专属参数。

## 为什么需要这条守卫

`resolve_browser_runtime()` 让浏览器内核可替换（Chromium / Firefox / WebKit）。
但 Chromium 的**无头保真参数**是它专属的命令行开关：

* `--disable-renderer-backgrounding`
* `--disable-features=CalculateNativeWinOcclusion`
* `channel="chrome-for-testing"`

这些传给 Firefox / WebKit 会**直接启动失败**。所以必须逐个断言：
非 Chromium 内核下，它们一个都不能出现。

## 判据

1. `chromium` 默认模式：channel 为 CFT、含无头保真参数（保持原行为，字节级不回退）；
2. `firefox` / `webkit`：**channel 必须为 None**，且 args 里不得有任何 Chromium 专属开关；
3. 显式 `executable_path`（用户自带浏览器）时不再套 CFT channel；
4. 无论哪个内核，TLS 策略（`verify_tls`）都要传达下去。

## 反向断言

把 `engine_family == "chromium"` 的判断改成恒真 ⇒ 非 Chromium 分支会带上
专属参数 ⇒ 本文件必红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
from omnicrawler.fetching.browser_launch import launch_kwargs, resolve_browser_runtime

#: Chromium 专属、绝不能出现在其它内核启动参数里的开关。
_CHROMIUM_ONLY = (
    "--disable-renderer-backgrounding",
    "CalculateNativeWinOcclusion",
)

_CFT_CHANNEL = "chrome-for-testing"


def _config(tls: bool = True) -> AppConfig:
    raw = deep_merge(dict(DEFAULTS), {"http": {"verify_tls": tls}})
    root = Path(".")
    return AppConfig(Path("<browser-runtime-test>"), root, raw, root)


def _kwargs(preferred_id: str | None, *, headless: bool = True, tls: bool = True) -> dict:
    spec = resolve_browser_runtime(_config(tls), preferred_id=preferred_id, headless=headless)
    return launch_kwargs(spec, headless=headless)


def test_default_is_chromium_with_cft_channel() -> None:
    """默认走 Chromium，且必须带 CFT channel（不打包 headless_shell 的约定）。"""
    kwargs = _kwargs(None)
    assert kwargs.get("channel") == _CFT_CHANNEL, (
        f"默认内核应使用 {_CFT_CHANNEL} channel，实际 {kwargs.get('channel')!r}"
    )


def test_chromium_keeps_headless_fidelity_args() -> None:
    """Chromium 无头模式必须保留保真参数（防止被后台降频）。"""
    args = _kwargs(None, headless=True).get("args") or []
    joined = " ".join(args)
    assert any(flag in joined for flag in _CHROMIUM_ONLY), (
        f"Chromium 无头模式缺少无头保真参数，实际 args={args}"
    )


@pytest.mark.parametrize("slot_id", ["browser-firefox", "browser-webkit"])
def test_non_chromium_drops_channel_and_chromium_args(slot_id: str) -> None:
    """切到 Firefox / WebKit 时，channel 与 Chromium 专属参数都必须消失。"""
    kwargs = _kwargs(slot_id)
    assert kwargs.get("channel") is None, (
        f"{slot_id} 不应带 CFT channel（那是 Chromium 专属），实际 {kwargs.get('channel')!r}"
    )
    args = kwargs.get("args") or []
    joined = " ".join(args)
    for flag in _CHROMIUM_ONLY:
        assert flag not in joined, (
            f"{slot_id} 的启动参数混入了 Chromium 专属开关 {flag!r}，"
            f"传给该内核会直接启动失败。实际 args={args}"
        )


def test_verify_tls_false_is_propagated_for_every_engine() -> None:
    """TLS 策略与内核无关：verify_tls=false 时必须传达到 spec。"""
    for slot_id in (None, "browser-firefox", "browser-webkit"):
        spec = resolve_browser_runtime(_config(False), preferred_id=slot_id, headless=True)
        assert spec.verify_tls is False, (
            f"{slot_id or 'chromium'} 未把 verify_tls=false 传达到运行时规格"
        )


def test_headless_false_drops_fidelity_args_even_for_chromium() -> None:
    """有头模式不需要保真参数——否则会传一堆无意义的开关给可见浏览器。"""
    kwargs = _kwargs(None, headless=False)
    joined = " ".join(kwargs.get("args") or [])
    assert "--disable-renderer-backgrounding" not in joined, (
        f"有头模式不应带无头保真参数，实际 {kwargs.get('args')}"
    )


def test_explicit_executable_path_skips_cft_channel(tmp_path: Path) -> None:
    """用户指定本机浏览器可执行文件时，不应再套 CFT channel。

    CFT channel 是「用 Playwright 自带那套 Chromium」的标志；用户给的是
    自己的 Chrome/Edge，套 channel 会指向错误的二进制。
    """
    fake = tmp_path / "my_chrome.exe"
    fake.write_bytes(b"")
    baseline = resolve_browser_runtime(_config(), headless=True)

    from omnicrawler.fetching.browser_launch import BrowserRuntimeSpec

    custom = BrowserRuntimeSpec(
        engine_family="chromium",
        driver_kind="playwright",
        channel=None,  # 指定了自带可执行文件 ⇒ 不套 CFT
        executable_path=str(fake),
        launch_args=tuple(baseline.launch_args),
        verify_tls=True,
    )
    kwargs = launch_kwargs(custom, headless=True)
    assert kwargs.get("channel") is None, (
        "指定了 executable_path 却仍套 CFT channel —— 会指向错误二进制"
    )
