"""Tests for extraction.markdown_reducer — HTML→Markdown 降维与语义分块."""

from __future__ import annotations

from omnicrawl.extraction.markdown_reducer import (
    html_to_markdown,
    reduce_for_llm,
    semantic_chunks,
)


def test_html_to_markdown_strips_scripts_and_nav() -> None:
    html = (
        "<html><head><script>var x=1;</script></head><body>"
        "<nav><a href='/x'>菜单</a></nav>"
        "<main><h1>标题</h1><p>正文段落</p></main></body></html>"
    )
    markdown = html_to_markdown(html)
    assert "var x=1" not in markdown
    assert "菜单" not in markdown
    assert "标题" in markdown
    assert "正文段落" in markdown
    assert "<nav>" not in markdown


def test_html_to_markdown_headings() -> None:
    html = "<h1>一级</h1><h2>二级</h2><h3>三级</h3>"
    markdown = html_to_markdown(html)
    assert "# 一级" in markdown
    assert "## 二级" in markdown
    assert "### 三级" in markdown


def test_html_to_markdown_links_and_image() -> None:
    html = "<p>去 <a href='https://example.com'>官网</a> 看看 <img src='/pic.png'></p>"
    markdown = html_to_markdown(html)
    assert "官网" in markdown
    assert "![image](/pic.png)" in markdown


def test_semantic_chunks_splits_by_heading() -> None:
    markdown = (
        "# 第一节\n内容一\n\n# 第二节\n内容二\n\n# 第三节\n内容三"
    )
    chunks = semantic_chunks(markdown, max_chars=500, max_chunks=10)
    # 总长度小于 max_chars 时按语义块粒度输出（每块一个标题段落）
    assert len(chunks) >= 1
    assert "# 第一节" in chunks[0]
    assert all(chunk for chunk in chunks)


def test_semantic_chunks_splits_when_exceeding_max() -> None:
    markdown = "\n\n".join(f"# 第{i}节\n" + ("内容" * 100) for i in range(5))
    chunks = semantic_chunks(markdown, max_chars=500, max_chunks=10)
    assert len(chunks) >= 2
    assert all(len(c) <= 800 for c in chunks)


def test_reduce_for_llm_empty_html() -> None:
    assert reduce_for_llm("") == []
    assert reduce_for_llm("<html><body></body></html>") == []


def test_reduce_for_llm_end_to_end() -> None:
    html = (
        "<html><body>"
        "<nav>导航垃圾</nav>"
        "<article><h1>新闻标题</h1>"
        "<p>这是正文，包含有用信息。</p>"
        "<p>更多细节内容。</p></article>"
        "</body></html>"
    )
    chunks = reduce_for_llm(html, max_chars=2000, max_chunks=5)
    assert any("新闻标题" in chunk for chunk in chunks)
    assert all("导航垃圾" not in chunk for chunk in chunks)
