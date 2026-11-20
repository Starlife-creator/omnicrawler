"""本地内核版本探测与驱动匹配提示的守卫。

## 这批守卫防什么

维护者要求支持「导入自己的浏览器内核」。这里有一条绕不过去的生态约束：

> Selenium 系的 **chromedriver 主版本必须与 Chrome 主版本完全一致**。

代码抹不平它，只能**如实提前告知**。所以探测必须准，提示必须具体到主版本号。

## 一个容易混的区分

Playwright 目录名 `chromium-1228` 里的 `1228` 是**构建号**，不是浏览器版本
（真实版本是 `149.0.7827.55`）。拿构建号当版本会让驱动提示指向错误的版本。

## 探测顺序的取舍

先读文件元数据、失败才执行 `--version`。因为执行**真的会拉起浏览器进程**，
实测在受限环境返回 `TransactNamedPipe: 管道已结束`（crashpad 起不来）。
导入流程不该以「启动一次浏览器」为默认代价。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawler.services.browser_kernel_probe import (
    KernelProbe,
    probe_kernel,
)

_CHROME = (
    Path(__file__).resolve().parents[3]
    / ".runtime"
    / "browsers"
    / "chromium-1228"
    / "chrome-win64"
    / "chrome.exe"
)


def test_missing_file_reports_warning() -> None:
    """文件不存在时给明确警告，**不抛异常**。

    导入流程要能把「版本未知」当成一个明确状态展示，而不是崩掉。
    """
    probe = probe_kernel("/definitely/not/here/chrome.exe")
    assert probe.ok is False
    assert probe.version == ""
    assert any("不存在" in w for w in probe.warnings)


def test_version_from_file_metadata_preferred() -> None:
    """Windows 上应能用文件元数据取到版本，**不启动进程**。"""
    if os.name != "nt" or not _CHROME.is_file():
        pytest.skip("需要本机已解的 Chromium（Windows）")
    probe = probe_kernel(_CHROME)
    assert probe.ok, f"没探测到版本：{probe.warnings}"
    assert probe.detected_by == "文件元数据", (
        f"用了 {probe.detected_by} —— 应优先读元数据，避免启动浏览器"
    )
    assert probe.major >= 100, f"主版本不合理：{probe.major}"


def test_build_number_is_not_treated_as_version() -> None:
    """**构建号不是版本号**：`chromium-1228` 里的 1228 是 Playwright 构建号。

    真实版本是 `149.x`。若把 1228 当主版本，驱动提示会指向一个不存在的版本。
    """
    if os.name != "nt" or not _CHROME.is_file():
        pytest.skip("需要本机已解的 Chromium（Windows）")
    probe = probe_kernel(_CHROME)
    assert probe.major != 1228, "把 Playwright 构建号当成了浏览器主版本"
    assert probe.major < 1000, f"主版本看起来是构建号：{probe.major}"


def test_engine_detected_from_name() -> None:
    """内核族必须能从文件名认出来（决定要不要驱动、要哪个）。"""
    assert probe_kernel("/x/firefox.exe").engine_family == "firefox"
    assert probe_kernel("/x/chrome.exe").engine_family == "chromium"
    assert probe_kernel("/x/chromium-1228/chrome").engine_family == "chromium"


def test_chromium_requires_chromedriver() -> None:
    """Chromium 必须提示 chromedriver。"""
    probe = probe_kernel("/x/chrome.exe")
    assert probe.required_driver == "chromedriver"


def test_firefox_requires_geckodriver() -> None:
    """Firefox 必须提示 geckodriver。"""
    probe = probe_kernel("/x/firefox.exe")
    assert probe.required_driver == "geckodriver"


def test_driver_hint_includes_major_version() -> None:
    """驱动提示必须**具体到主版本号** —— 只说「要匹配的驱动」等于没说。"""
    probe = KernelProbe(
        path=Path("/x/chrome.exe"),
        version="149.0.7827.55",
        major=149,
        engine_family="chromium",
        required_driver="chromedriver",
    )
    hint = probe.driver_hint()
    assert "149" in hint, f"提示没给出主版本号：{hint}"
    assert "chromedriver" in hint


def test_driver_hint_admits_unknown_version() -> None:
    """版本未知时如实说「请手动确认」，不编一个版本号。"""
    probe = KernelProbe(
        path=Path("/x/chrome.exe"),
        engine_family="chromium",
        required_driver="chromedriver",
    )
    hint = probe.driver_hint()
    assert "未能探测" in hint or "手动确认" in hint
    assert not any(ch.isdigit() for ch in hint.replace("x", "")), (
        f"版本未知却给出了数字：{hint}"
    )


def test_no_driver_needed_for_webkit() -> None:
    """WebKit 不需要外部驱动（Playwright 自带），不能瞎提示。"""
    probe = probe_kernel("/x/webkit")
    assert probe.required_driver == ""


def test_as_dict_is_serialisable() -> None:
    """`as_dict()` 必须可序列化（CLI 的 `--json` 依赖它）。"""
    probe = KernelProbe(path=Path("/x/chrome.exe"), version="149.0.1")
    payload = probe.as_dict()
    assert payload["path"]
    assert "driver_hint" in payload
    assert isinstance(payload["warnings"], list)


def test_executable_with_no_version_reports_warning() -> None:
    """探测不到版本时要带警告，不能静默返回空。"""
    # 用一个真实存在但不是浏览器的文件
    target = Path(__file__)
    probe = probe_kernel(target)
    assert probe.ok is False
    assert any("未能探测版本" in w for w in probe.warnings)
