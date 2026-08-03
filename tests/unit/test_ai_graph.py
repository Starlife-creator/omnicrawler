"""Tests for extraction.ai_graph — chunk strategy, JSON parse, merge logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from omnicrawl.extraction.ai_graph import (
    AIGraphExtractor,
    FieldDef,
    Provider,
    SplitStrategy,
)

# ── SplitStrategy ──────────────────────────────────────────────────────

class TestSplitStrategy:
    def test_enum_values(self) -> None:
        assert SplitStrategy.AUTO.value == "auto"
        assert SplitStrategy.HEADING.value == "heading"
        assert SplitStrategy.FIXED_CHUNK.value == "fixed_chunk"


# ── FieldDef ───────────────────────────────────────────────────────────

class TestFieldDef:
    def test_defaults(self) -> None:
        fd = FieldDef(name="title")
        assert fd.name == "title"
        assert fd.description == ""
        assert fd.example == ""
        assert fd.required is False
        assert fd.field_type == "text"

    def test_full(self) -> None:
        fd = FieldDef(name="price", description="价格", example="99.9", required=True, field_type="number")
        assert fd.field_type == "number"


# ── Provider ───────────────────────────────────────────────────────────

class TestProvider:
    def test_defaults(self) -> None:
        p = Provider()
        assert p.base_url == "https://api.openai.com/v1"
        assert p.api_key == ""
        assert p.model == "gpt-4o"


# ── AIGraphExtractor — chunk splitting ─────────────────────────────────

class TestSplitHtml:
    def test_short_html_no_split(self) -> None:
        ex = AIGraphExtractor(chunk_size=4000)
        html = "<html><body>short</body></html>"
        chunks = ex._split_html(html, SplitStrategy.AUTO)
        assert len(chunks) == 1
        assert chunks[0] == html

    def test_fixed_chunk_split(self) -> None:
        ex = AIGraphExtractor(chunk_size=500)  # minimum clamped
        html = "a" * 1200  # > 500*2 = 1000
        chunks = ex._split_html(html, SplitStrategy.FIXED_CHUNK)
        assert len(chunks) >= 2

    def test_auto_strategy_falls_back_to_fixed(self) -> None:
        ex = AIGraphExtractor(chunk_size=500)
        html = "x" * 1200
        chunks = ex._split_html(html, SplitStrategy.AUTO)
        assert len(chunks) >= 2

    def test_heading_split_with_headings(self) -> None:
        ex = AIGraphExtractor()
        html = "<h1>Title1</h1>Content1<h2>Title2</h2>Content2"
        chunks = ex._split_html(html, SplitStrategy.HEADING)
        assert len(chunks) == 2
        assert "Title1" in chunks[0]
        assert "Title2" in chunks[1]

    def test_heading_split_no_headings_falls_back(self) -> None:
        ex = AIGraphExtractor(chunk_size=4000)
        html = "<div>no headings here</div>"
        chunks = ex._split_html(html, SplitStrategy.HEADING)
        assert len(chunks) == 1

    def test_empty_html_returns_single_empty_chunk(self) -> None:
        ex = AIGraphExtractor()
        chunks = ex._split_html("", SplitStrategy.AUTO)
        # empty string produces no chunks, so falls back to [""]
        assert chunks == [""]

    def test_chunk_size_clamped_to_500_minimum(self) -> None:
        ex = AIGraphExtractor(chunk_size=100)
        assert ex._chunk_size == 500

    def test_chunk_size_clamped_to_32000_maximum(self) -> None:
        ex = AIGraphExtractor(chunk_size=99999)
        assert ex._chunk_size == 32000


# ── AIGraphExtractor — fields spec ─────────────────────────────────────

class TestBuildFieldsSpec:
    def test_basic_fields(self) -> None:
        ex = AIGraphExtractor()
        fields = [FieldDef(name="title", description="文章标题")]
        spec = ex._build_fields_spec(fields)
        assert "title" in spec
        assert "text" in spec
        assert "文章标题" in spec

    def test_with_example_and_required(self) -> None:
        ex = AIGraphExtractor()
        fields = [FieldDef(name="price", description="价格", example="99.9", required=True, field_type="number")]
        spec = ex._build_fields_spec(fields)
        assert "如: 99.9" in spec
        assert "[必填]" in spec
        assert "number" in spec

    def test_field_without_description(self) -> None:
        ex = AIGraphExtractor()
        fields = [FieldDef(name="raw")]
        spec = ex._build_fields_spec(fields)
        assert "raw" in spec


# ── AIGraphExtractor — parse_response ──────────────────────────────────

class TestParseResponse:
    def test_valid_json(self) -> None:
        ex = AIGraphExtractor()
        result = ex._parse_response('{"fields": {"title": "Hello"}, "confidence": 0.95}')
        assert result["fields"] == {"title": "Hello"}
        assert result["confidence"] == 0.95

    def test_json_in_markdown_code_block(self) -> None:
        ex = AIGraphExtractor()
        content = '```json\n{"fields": {"x": 1}, "confidence": 0.8}\n```'
        result = ex._parse_response(content)
        assert result["fields"] == {"x": 1}

    def test_inline_code_block(self) -> None:
        ex = AIGraphExtractor()
        content = '```\n{"fields": {"x": 1}, "confidence": 0.8}\n```'
        result = ex._parse_response(content)
        assert result["fields"] == {"x": 1}

    def test_extract_json_from_surrounding_text(self) -> None:
        ex = AIGraphExtractor()
        content = 'Here is the result: {"fields": {"name": "Bob"}, "confidence": 0.7}. Done.'
        result = ex._parse_response(content)
        assert result["fields"] == {"name": "Bob"}
        assert result["confidence"] == 0.7

    def test_unparseable_returns_default(self) -> None:
        ex = AIGraphExtractor()
        result = ex._parse_response("just some random text with no json")
        assert result == {"fields": {}, "confidence": 0.0}


# ── AIGraphExtractor — merge_results ───────────────────────────────────

class TestMergeResults:
    def test_merge_two_chunks(self) -> None:
        ex = AIGraphExtractor()
        results = [
            {"fields": {"title": "Hello"}, "confidence": 0.9},
            {"fields": {"title": "Better", "price": "10"}, "confidence": 0.8},
        ]
        merged = ex._merge_results(results, 3)
        assert merged["fields"] == {"title": "Hello", "price": "10"}
        assert merged["confidence"] == 0.85  # (0.9 + 0.8) / 2
        assert merged["chunks_processed"] == 2
        assert merged["total_chunks"] == 3

    def test_first_value_wins_on_duplicate(self) -> None:
        ex = AIGraphExtractor()
        results = [
            {"fields": {"a": "first"}, "confidence": 0.5},
            {"fields": {"a": "second"}, "confidence": 0.6},
        ]
        merged = ex._merge_results(results, 2)
        assert merged["fields"] == {"a": "first"}

    def test_empty_value_overridden(self) -> None:
        ex = AIGraphExtractor()
        results = [
            {"fields": {"a": ""}, "confidence": 0.5},
            {"fields": {"a": "real_value"}, "confidence": 0.6},
        ]
        merged = ex._merge_results(results, 2)
        assert merged["fields"] == {"a": "real_value"}

    def test_no_results_returns_zero_confidence(self) -> None:
        ex = AIGraphExtractor()
        merged = ex._merge_results([], 5)
        assert merged["fields"] == {}
        assert merged["confidence"] == 0.0
        assert merged["chunks_processed"] == 0

    def test_confidence_as_int(self) -> None:
        ex = AIGraphExtractor()
        results = [{"fields": {}, "confidence": 1}]
        merged = ex._merge_results(results, 1)
        assert merged["confidence"] == 1.0

    def test_fields_not_a_dict_is_skipped(self) -> None:
        ex = AIGraphExtractor()
        results = [{"fields": "not a dict", "confidence": 0.5}]
        merged = ex._merge_results(results, 1)
        assert merged["fields"] == {}


# ── AIGraphExtractor — extract (async) ─────────────────────────────────

class TestExtractAsync:
    @pytest.mark.asyncio
    async def test_extract_single_chunk(self) -> None:
        ex = AIGraphExtractor(chunk_size=4000)
        html = "<html>short</html>"
        fields = [FieldDef(name="title")]
        mock_result = {"fields": {"title": "Test"}, "confidence": 0.9}
        with patch.object(ex, "_extract_chunk", new_callable=AsyncMock, return_value=mock_result):
            result = await ex.extract(html, fields)
            assert result["fields"] == {"title": "Test"}
            assert result["chunks_processed"] == 1

    @pytest.mark.asyncio
    async def test_extract_with_failed_chunk_skipped(self) -> None:
        ex = AIGraphExtractor(chunk_size=10)
        html = "a" * 25  # will be split into 3 chunks
        fields = [FieldDef(name="title")]
        ok = {"fields": {"title": "OK"}, "confidence": 0.8}
        async def side_effect(html, fields, max_tokens):
            if "aaaaa" in html:
                return ok
            raise RuntimeError("chunk failed")
        with patch.object(ex, "_extract_chunk", side_effect=side_effect):
            result = await ex.extract(html, fields)
            assert result["fields"] == {"title": "OK"}

    @pytest.mark.asyncio
    async def test_extract_single_page(self) -> None:
        ex = AIGraphExtractor()
        html = "<html>page</html>"
        fields = [FieldDef(name="title")]
        mock = {"fields": {"title": "SP"}, "confidence": 1.0}
        with patch.object(ex, "_extract_chunk", new_callable=AsyncMock, return_value=mock):
            result = await ex.extract_single_page(html, fields)
            assert result["fields"] == {"title": "SP"}


# ── Backward-compatible aliases ────────────────────────────────────────

class TestAliases:
    def test_provider_alias_on_class(self) -> None:
        assert AIGraphExtractor.Provider is Provider

    def test_fielddef_alias_on_class(self) -> None:
        assert AIGraphExtractor.FieldDef is FieldDef
