"""`LogConsole._redact_log`：脱敏不许**伪造**目标域名。

为什么要有这个文件：导出日志前会对 URL 脱敏，原实现把主机名换成 `***.com` ——
那等于**伪造了一个具体域名**。用户排查「请求到底发往哪」时看到 `.com`，
会误以为是真实的公网地址，而原值可能是 `intranet.local` / `[::1]` / 内网 IP。

实测（2026-11-24）：`https://intranet.local:8443/secret/path` 被脱成
`https://***.com/secret/path`，看着像公网。已改为中性的 `***`。

本文件钉住：主机名被遮蔽、**不谎报后缀**、路径与其余诊断信息保留。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.widgets.log_console import LogConsole  # noqa: E402


@pytest.fixture(scope="module")
def console():
    """不调用 __init__（会建控件树），只要 `_redact_log` 这个纯函数。"""
    app = QApplication.instance() or QApplication([])
    widget = LogConsole.__new__(LogConsole)
    yield widget
    del app


@pytest.mark.parametrize(
    "url",
    [
        "https://intranet.local:8443/secret/path",
        "https://[::1]:8080/x",
        "https://10.0.0.5/internal",
    ],
)
def test_internal_hosts_are_not_disguised_as_public(console, url: str) -> None:
    """内网主机不得被脱成看起来像公网的形态。"""
    out = console._redact_log(url)
    assert ".com" not in out, f"脱敏后出现了伪装的公网后缀：{out}"
    assert "***" in out


def test_path_and_query_structure_survive(console) -> None:
    """路径要保留 —— 否则无法判断「当时请求的是哪个接口」。"""
    out = console._redact_log("https://api.internal.example/v1/data?id=1")
    assert "/v1/data" in out
    assert "api.internal.example" not in out


def test_original_hostname_is_never_present(console) -> None:
    """主机名本身必须消失。"""
    out = console._redact_log("https://secret-host.example/x")
    assert "secret-host" not in out


def test_selector_value_is_redacted(console) -> None:
    """CSS 选择器的内容属于站点结构，也要遮。"""
    out = console._redact_log("selector='div.price > span'")
    assert "div.price" not in out


def test_plain_text_without_sensitive_content_is_unchanged(console) -> None:
    """普通日志行不该被改动 —— 否则导出日志失去可读性。"""
    text = "处理完成：共 12 条记录"
    assert console._redact_log(text) == text
