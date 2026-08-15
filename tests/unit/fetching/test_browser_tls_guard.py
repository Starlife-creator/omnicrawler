"""B03-006：浏览器路径 TLS 一致性——Selenium launch_args 禁止关闭 TLS 校验。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.browser_fetcher import BrowserFetcher


def _config(tmp_path: Path, *, launch_args: list[str], verify_tls: bool = True) -> object:
    config_path = tmp_path / "task.yaml"
    args_line = "launch_args: [" + ", ".join(repr(a) for a in launch_args) + "]"
    config_path.write_text(
        "project: {name: b306, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"browser: {{{args_line}}}\n"
        f"http: {{verify_tls: {str(verify_tls).lower()}}}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


def _fake_selenium(monkeypatch, captured_args: list[str]) -> None:
    """用 fake selenium 包替代真实浏览器，仅验证启动前校验（不真正启动 Chrome）。"""

    class FakeOptions:
        def __init__(self) -> None:
            self.enable_bidi = False
            self.binary_location = ""

        def add_argument(self, argument: str) -> None:
            captured_args.append(argument)

    class FakeService:
        def __init__(self, *args, **kwargs) -> None:
            pass

    service = types.ModuleType("selenium.webdriver.chrome.service")
    service.Service = FakeService
    chrome = types.ModuleType("selenium.webdriver.chrome")
    chrome.service = service
    webdriver = types.ModuleType("selenium.webdriver")
    webdriver.ChromeOptions = FakeOptions
    webdriver.chrome = chrome
    selenium_mod = types.ModuleType("selenium")
    selenium_mod.webdriver = webdriver
    for name, mod in (
        ("selenium", selenium_mod),
        ("selenium.webdriver", webdriver),
        ("selenium.webdriver.chrome", chrome),
        ("selenium.webdriver.chrome.service", service),
    ):
        monkeypatch.setitem(sys.modules, name, mod)


def test_selenium_launch_args_reject_tls_disable(tmp_path: Path, monkeypatch) -> None:
    """launch_args 含 --ignore-certificate-errors 必须在启动浏览器前被拒。"""
    captured: list[str] = []
    _fake_selenium(monkeypatch, captured)
    config = _config(tmp_path, launch_args=["--ignore-certificate-errors"])
    fetcher = BrowserFetcher(config)
    with pytest.raises(ValueError, match="禁止关闭 TLS"):
        fetcher._selenium(CrawlRequest("https://example.org/"))
    # 校验发生在 add_argument 之前，TLS 关闭参数不应被加入
    assert "--ignore-certificate-errors" not in captured


def test_selenium_launch_args_reject_tls_disable_equals_form(tmp_path: Path, monkeypatch) -> None:
    """`--ignore-certificate-errors=true` 前缀形态同样被拒。"""
    captured: list[str] = []
    _fake_selenium(monkeypatch, captured)
    config = _config(tmp_path, launch_args=["--ignore-certificate-errors=true"])
    fetcher = BrowserFetcher(config)
    with pytest.raises(ValueError, match="禁止关闭 TLS"):
        fetcher._selenium(CrawlRequest("https://example.org/"))


def test_selenium_verify_tls_false_adds_ignore_flag(tmp_path: Path, monkeypatch) -> None:
    """http.verify_tls=false（可审计开关）时添加 --ignore-certificate-errors。"""
    captured: list[str] = []
    _fake_selenium(monkeypatch, captured)
    config = _config(tmp_path, launch_args=[], verify_tls=False)
    fetcher = BrowserFetcher(config)
    # _selenium 后续会因缺少真实 chromedriver 失败；TLS 参数在 driver 启动前已加入，
    # 因此只需确保调用不抛 ValueError（非拒绝路径）且 captured 含 TLS 关闭参数。
    try:
        fetcher._selenium(CrawlRequest("https://example.org/"))
    except Exception:
        pass
    assert "--ignore-certificate-errors" in captured
