"""S2.5.14：extractors 正则/JSON 容错 + field_designer 性能。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.core.safe_data import safe_regex_search
from omnicrawler.extraction.extractors import JSONProcessor
from omnicrawler.extraction.field_designer import analyze_html

# ── safe_regex_search ─────────────────────────────────────────────────

def test_safe_regex_compilation_error_returns_none() -> None:
    assert safe_regex_search("(unclosed", "text") is None


def test_safe_regex_matches_normally() -> None:
    match = safe_regex_search(r"price[:：]?\s*(\d+)", "price: 123")
    assert match is not None and match.group(1) == "123"


def test_safe_regex_catastrophic_pattern_rejected() -> None:
    # (a+)+ 型灾难性回溯模式：执行前拒绝，返回 None 而非卡死
    pattern = r"^(a+)+$"
    assert safe_regex_search(pattern, "a" * 64 + "b") is None


# ── extractors group 越界防护 ─────────────────────────────────────────

def test_regex_group_out_of_range_skipped(tmp_path: Path) -> None:

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2514, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract: {mode: html, fields: {f: {selector: p, regex: '(\\\\d+)', group: 99}}}\n",
        encoding="utf-8",
    )
    from omnicrawler.core.config import load_config

    config = load_config(config_path)
    processor = config.processors["html"] if hasattr(config, "processors") else None
    if processor is None:
        from omnicrawler.extraction.extractors import HTMLProcessor

        processor = HTMLProcessor(config)
    result = FetchResult(
        CrawlRequest("https://example.org/"), "https://example.org/", 200,
        {"content-type": "text/html"}, b"<html><body><p>abc 42</p></body></html>", 0.1,
    )
    parsed = processor.process(result)
    assert len(parsed.records) == 0  # 越界 group 跳过而非崩溃


# ── JSONProcessor URL 上下文 ──────────────────────────────────────────

def test_json_processor_error_mentions_url() -> None:
    class _FakeConfig:
        section = lambda self, _name: {"mode": "json", "item_path": "$", "fields": {}}  # noqa: E731

    processor = JSONProcessor(_FakeConfig())  # type: ignore[arg-type]
    result = FetchResult(
        CrawlRequest("https://example.org/not-json"), "https://example.org/not-json", 200,
        {"content-type": "application/json"}, b"not json at all", 0.1,
    )
    with pytest.raises(ValueError, match="https://example.org/not-json"):
        processor.process(result)


# ── field_designer 节点上限 + 非 O(n²) ────────────────────────────────

def test_analyze_html_limits_nodes() -> None:

    rows = "".join(f"<tr><td>cell {i}</td></tr>" for i in range(3000))
    html = f"<html><body><table>{rows}</table></body></html>"
    candidates = analyze_html(html)
    assert isinstance(candidates, list)


def test_nth_of_type_ordinal_is_exact_for_late_siblings() -> None:
    """尾部兄弟的 `:nth-of-type(n)` 序号必须精确——序号错位会让选择器指到别的元素。

    历史坑：修复 O(n²) 时曾用 `id(element)` 当「同标签兄弟序号表」的键，
    但迭代产生的包装对象一旦被回收，`id` 会被复用 ⇒ 查表错位
    （差分测试实测 267 个元素里 205 个不一致）。键必须是元素对象本身。
    """
    from lxml import html as lxml_html

    from omnicrawler.extraction.field_designer import _css_selector

    rows = "".join(f"<tr><td>cell {i}</td></tr>" for i in range(300))
    root = lxml_html.fromstring(f"<html><body><table>{rows}</table></body></html>")
    trs = root.xpath("//tr")
    assert len(trs) == 300, f"样本没造出 300 个 tr（{len(trs)} 个）"
    cache: dict = {}
    first_css, _ = _css_selector(trs[0], cache)
    last_css, _ = _css_selector(trs[-1], cache)
    assert first_css.endswith("tr:nth-of-type(1)"), first_css
    assert last_css.endswith("tr:nth-of-type(300)"), last_css


def test_sibling_index_is_built_once_per_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """性能守卫：同标签兄弟序号必须**按父元素**缓存，不得按元素重扫兄弟。

    反例（修复前的实现）：对第 k 个元素现筛兄弟并 `.index()` ⇒ 整篇文档 O(n²)，
    且 `lxml.html` 迭代时每个孩子都要过一次 `lookup()` 包装，常数很大
    （实测 3000 行表格单测 5.8s，`lookup` 被调 1800 万次）。

    反向断言（已实测）：去掉 `_sibling_index` 的缓存、每次重算 ⇒ 本用例转红。
    """
    from omnicrawler.extraction import field_designer

    calls: list[object] = []
    real = field_designer._enumerate_children

    def counting(parent: object) -> dict:
        calls.append(parent)
        return real(parent)

    monkeypatch.setattr(field_designer, "_enumerate_children", counting)

    rows = "".join(f"<tr><td>cell {i}</td></tr>" for i in range(300))
    field_designer.analyze_html(f"<html><body><table>{rows}</table></body></html>")

    assert calls, "守卫失去意义：一次都没走到枚举（说明缓存路径没被用上）"
    repeated = len(calls) - len(set(calls))
    assert repeated == 0, (
        f"同一父元素被重复枚举 {repeated} 次（{len(calls)} 次调用 / {len(set(calls))} 个父元素）"
        " ⇒ 兄弟序号没有按父元素缓存，复杂度退回 O(n²)"
    )


def test_analyze_html_returns_ranked_candidates() -> None:
    html = """
    <html><body>
      <h1>Company Report</h1>
      <p class="price">1,200.00</p>
      <time datetime="2024-03-01">2024年3月1日</time>
      <a href="/about" id="about">About us</a>
    </body></html>
    """
    candidates = analyze_html(html, limit=20)
    assert candidates
    names = {item.suggested_name for item in candidates}
    assert "price" in names
    assert "date" in names
    assert all(item.score >= 0.25 for item in candidates)
