"""能力槽位可用性判据的守卫。

## 这批守卫防什么

`browser` 槽位注册了 6 个 provider，但**三个 Playwright 内核共用同一个
`_spec_available("playwright")`** —— 它只检查 Python 包是否安装，
完全不看内核二进制在不在。

实测（2026-11-20 本机）：

| provider | 修复前 | 实际 |
|---|---|---|
| `playwright`（chromium） | True | 有 `chromium-1228` |
| `playwright-firefox` | **True** | **二进制不存在** |
| `playwright-webkit` | **True** | **二进制不存在** |

用户看到「可用」、选了它、运行时才失败 —— 正是 §15.2「声明必须能从字节
推导」要禁止的那类问题，只不过这次发生在能力槽位上。

同理 `selenium` / `selenium-firefox` 的描述自己写着「需配合 XDriver 运行」，
探测却只查 Python 包。

## 判据

* Playwright 内核：包已安装 **且** `ms-playwright/` 下有该内核目录；
* Selenium：包已安装 **且** 驱动可执行文件在 PATH 上；
* **找不到浏览器缓存目录时判 False**（fail-closed）——
  「看不出有没有」不能当成「有」。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawler.core.capability_slots import (
    _PLAYWRIGHT_ENGINE_DIRS,
    _playwright_browser_root,
    _playwright_engine_available,
    _webdriver_available,
)


def test_engine_dir_map_covers_three_kernels() -> None:
    """三个内核都必须有目录前缀，否则探测永远返回 False（静默失效）。"""
    for engine in ("chromium", "firefox", "webkit"):
        assert engine in _PLAYWRIGHT_ENGINE_DIRS, f"{engine} 没有目录前缀"
        assert _PLAYWRIGHT_ENGINE_DIRS[engine], f"{engine} 的前缀列表为空"


def test_unknown_engine_is_false() -> None:
    """未登记的内核名必须判 False（不能因为查不到就放行）。"""
    assert _playwright_engine_available("netscape") is False


def test_missing_browser_root_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """**找不到浏览器缓存目录时必须判 False**（fail-closed）。

    早先的实现对 firefox/webkit 恒返回 True，因为只查 Python 包。
    「看不出有没有」不能当成「有」—— 那会让用户选了根本跑不起来的内核。
    """
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent/path/xyz")
    assert _playwright_browser_root() is None
    for engine in ("chromium", "firefox", "webkit"):
        assert _playwright_engine_available(engine) is False


def test_engine_present_only_when_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有装了该内核目录才算可用。"""
    (tmp_path / "chromium-1228").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    assert _playwright_engine_available("chromium") is True, "装了 chromium 却报不可用"
    assert _playwright_engine_available("firefox") is False, "没装 firefox 却报可用"
    assert _playwright_engine_available("webkit") is False, "没装 webkit 却报可用"


def test_headless_shell_counts_as_chromium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chromium_headless_shell-*` 也是可用的 Chromium 形态。"""
    (tmp_path / "chromium_headless_shell-1228").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert _playwright_engine_available("chromium") is True


def test_webdriver_missing_is_false() -> None:
    """驱动不在 PATH 上时必须判 False。"""
    assert _webdriver_available("definitely-not-a-driver") is False


def test_unknown_driver_is_false() -> None:
    """未登记的驱动名必须判 False。"""
    assert _webdriver_available("netscape_driver") is False


@pytest.mark.skipif(
    os.environ.get("OMNICRAWLER_EXPECT_NO_GECKO") != "1",
    reason="需要显式声明本机没有 geckodriver 才验证（否则结果依赖环境）",
)
def test_geckodriver_absent_reports_unavailable() -> None:
    """本机没有 geckodriver 时，`selenium-firefox` 必须报不可用。"""
    assert _webdriver_available("geckodriver") is False


def test_browser_slot_reports_truthfully() -> None:
    """`browser` 槽位的 `available()` **不得**包含二进制缺失的内核。

    这条用真实环境。若本机没装 firefox/webkit，它们就不该出现在 available 里。
    """
    from omnicrawler.core.capabilities import default_capability_slots

    slot = default_capability_slots(refresh=True).slot("browser")
    assert slot is not None
    available_ids = {p.provider_id for p in slot.available()}

    root = _playwright_browser_root()
    has_firefox = False
    if root is not None:
        has_firefox = any(
            child.is_dir() and child.name.startswith("firefox-")
            for child in root.iterdir()
        )

    if not has_firefox:
        assert "playwright-firefox" not in available_ids, (
            "没装 Firefox 二进制却报告 playwright-firefox 可用"
        )
