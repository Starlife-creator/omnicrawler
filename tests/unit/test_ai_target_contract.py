"""Target schema guards exercise public extraction without a real model."""
from unittest.mock import AsyncMock, patch

import pytest

from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, Provider


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    '{"fields":{"price":true}}',
    '{"fields":{"price":"12"}}',
    '{"fields":{"extra":"x"}}',
    '{"fields":{"price":12},"confidence":true}',
    '{"fields":{"price":12},"confidence":1.5}',
])
async def test_invalid_model_fields_are_not_success(body, monkeypatch):
    monkeypatch.setattr("omnicrawler.core.ai_env.require_ai_privacy", lambda *a, **k: None)
    ex = AIGraphExtractor(provider=Provider(api_key="test"))
    response = {"choices": [{"message": {"content": body}}]}
    with patch.object(ex, "_post_with_retry", new_callable=AsyncMock, return_value=response):
        with pytest.raises(ValueError):
            await ex._extract_chunk("<p>12</p>", [FieldDef("price", field_type="number")], 100, session=object())


@pytest.mark.asyncio
async def test_required_fields_checked_after_merge_and_conflicts_need_review():
    ex = AIGraphExtractor(chunk_size=500)
    fields = [FieldDef("title", required=True), FieldDef("price", required=True, field_type="number")]
    responses = [{"fields": {"title": "A"}, "confidence": 1, "evidence": {"title": {"quote": "A"}}},
                 {"fields": {"price": 0}, "confidence": 1, "evidence": {"price": {"quote": "0"}}}]
    with patch.object(ex, "_extract_chunk", new_callable=AsyncMock, side_effect=responses):
        result = await ex.extract("<p>A</p>" + "a" * 500 + "<p>0</p>", fields)
    assert result["fields"]["price"] == 0
    assert result["review_required"] is False
    with patch.object(ex, "_extract_chunk", new_callable=AsyncMock, return_value={"fields": {"price": 0}}):
        result = await ex.extract("a", fields)
    assert result["missing_required"] == ["title"]
    assert result["review_required"] is True
    responses = [{"fields": {"title": "A", "price": 0}}, {"fields": {"title": "A", "price": 1}}]
    with patch.object(ex, "_extract_chunk", new_callable=AsyncMock, side_effect=responses):
        result = await ex.extract("a" * 600, fields)
    assert result["review_required"] is True
    assert result["conflicts"][0]["field"] == "price"
