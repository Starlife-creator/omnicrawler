"""`auto-analyze --request` 的两处真实缺陷（2026-11-27）。

## 缺陷 1：用文件输入时永远报「网址不一致」

`auto-analyze` 支持「本地 HTML 文件 + `--url` 指定原始网址」（离线夹具、抓下来再分析
都是这个用法）。旧实现拿需求里的网址去比 `args.input`（**那是文件路径**），于是
只要用文件输入就一定报错，而提示里的修复建议（去 `source.seeds` 补网址）也无从下手。

## 缺陷 2：`--request` 收窄字段后，自检报告仍是旧的

`analyze_to_config` 会把验证报告登记下来供调用方复用。`apply_task_goal` 之后
`config["extract"]["fields"]` 被**原地收窄**，但配置对象身份与 HTML 都没变 ——
旧判据（id + html 哈希）判定"可以复用"，于是报告里还是 4 个字段，而实际只该有 2 个。
用户看到的是「已验证: 字段 {编号, 名称, 价格, 品牌}」，但他只要求了两个。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from omnicrawler.cli import _handlers
from omnicrawler.extraction.intelligent_scraper import (
    analyze_to_config,
    apply_task_goal,
    remember_report,
    verified_report,
    verify_config,
)

SPA_HTML = (
    '<html><head><script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"products":['
    '{"id":1,"name":"Alpha","price":9.9,"brand":"Acme"},'
    '{"id":2,"name":"Beta","price":19.9,"brand":"Acme"},'
    '{"id":3,"name":"Gamma","price":29.9,"brand":"Beta"}'
    ']}}}'
    '</script></head><body><div id="__next"></div></body></html>'
)


# ── 缺陷 1：文件输入 + --url ────────────────────────────────────────────


def test_request_with_file_input_and_matching_url_is_accepted(tmp_path: Path) -> None:
    """★ 这条在修复前必定抛「需求中的入口网址与分析网址不一致」。"""
    html_path = tmp_path / "page.html"
    html_path.write_text(SPA_HTML, encoding="utf-8")
    out = tmp_path / "out.yaml"
    args = argparse.Namespace(
        input=str(html_path),
        url="https://example.org/shop",
        request="从 https://example.org/shop 抓取名称和价格，导出 Excel",
        output=str(out),
        always_browser=False,
        item_path="",
    )
    _handlers._run_auto_analyze(args)
    assert out.is_file(), "没有写出配置"
    text = out.read_text(encoding="utf-8")
    assert "名称" in text and "价格" in text
    # 收窄生效：编号 / 品牌 不该出现在最终字段里
    assert "编号:" not in text, text[:800]


def test_request_with_genuinely_different_url_is_still_rejected(tmp_path: Path) -> None:
    """反方向：需求写的网址与分析网址**真的**不同时，必须继续拒绝。

    放宽成"不检查"会让用户以为在抓 A，实际抓的是 B —— 那比报错糟得多。
    """
    html_path = tmp_path / "page.html"
    html_path.write_text(SPA_HTML, encoding="utf-8")
    args = argparse.Namespace(
        input=str(html_path),
        url="https://example.org/shop",
        request="从 https://example.org/other 抓取名称和价格",
        output=str(tmp_path / "out.yaml"),
        always_browser=False,
        item_path="",
    )
    with pytest.raises(ValueError, match="不一致"):
        _handlers._run_auto_analyze(args)


# ── 缺陷 2：配置被原地改过，报告必须失效 ────────────────────────────────


def test_report_is_not_reused_after_fields_are_narrowed() -> None:
    """★ `apply_task_goal` 原地改 fields 之后，旧报告必须**不再复用**。

    修复前判据只有 id + html 哈希，两者都没变 ⇒ 复用旧报告 ⇒ 用户看到 4 个字段，
    而实际只有 2 个（一条关于"我到底抓了什么"的错误陈述）。
    """
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    before = verified_report(config, SPA_HTML)
    assert before is not None and len(before["fields"]) == 4, before
    apply_task_goal(
        config,
        {
            "fields": ["名称", "价格"],
            "multi_value_fields": [],
            "max_pages": 1,
            "output_formats": ["jsonl"],
        },
    )
    assert verified_report(config, SPA_HTML) is None, "收窄字段后仍复用了旧报告"
    fresh = verify_config(config, SPA_HTML)
    assert set(fresh["fields"]) == {"名称", "价格"}, fresh


def test_report_reuse_still_works_when_nothing_changed() -> None:
    """反方向：配置没动时仍要复用（否则这条优化就没了）。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    first = verified_report(config, SPA_HTML)
    assert first is not None
    assert verified_report(config, SPA_HTML) is first


def test_signature_ignores_unrelated_sections() -> None:
    """签名只看 `extract` —— 改 `crawl` 不该让报告失效（那与验证无关）。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    report = verified_report(config, SPA_HTML)
    assert report is not None
    config["crawl"]["max_pages"] = 999
    assert verified_report(config, SPA_HTML) is report


def test_manual_remember_still_works() -> None:
    config = {"extract": {"mode": "html", "fields": {}}}
    remember_report(config, "<html></html>", {"mode": "html", "items": 1, "records": 1, "fields": {}})
    assert verified_report(config, "<html></html>") == {
        "mode": "html", "items": 1, "records": 1, "fields": {},
    }
