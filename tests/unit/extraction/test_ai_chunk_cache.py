import pytest

from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef


@pytest.mark.asyncio
async def test_cache_reuses_candidates_but_not_accounting_and_invalidates_rules(monkeypatch):
    monkeypatch.setattr("omnicrawler.core.ai_env.require_ai_privacy", lambda *args, **kwargs: None)
    extractor = AIGraphExtractor(cache_entries=2)
    calls = []
    async def produce(*args, **kwargs):
        calls.append(1)
        return {"fields": {"amount": 10}, "accounting": {"cost": 1}, "confidence": .9}
    monkeypatch.setattr(extractor, "_extract_chunk_uncached", produce)
    fields = [FieldDef("amount", field_type="number")]
    first = await extractor._extract_chunk("Amount 10", fields, 100)
    first["fields"]["amount"] = 20
    second = await extractor._extract_chunk("Amount 10", fields, 100)
    assert len(calls) == 1 and second["fields"]["amount"] == 10
    assert second["cache_hit"] and "accounting" not in second
    extractor._rule_version = "2"
    await extractor._extract_chunk("Amount 10", fields, 100)
    assert len(calls) == 2
    await extractor._extract_chunk("Amount 10", [FieldDef("amount", description="USD")], 100)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_cache_hit_still_respects_revoked_privacy(monkeypatch):
    monkeypatch.setattr("omnicrawler.core.ai_env.require_ai_privacy", lambda *args, **kwargs: None)
    extractor = AIGraphExtractor(cache_entries=1)
    async def produce(*args, **kwargs):
        return {"fields": {"title": "A"}}
    monkeypatch.setattr(extractor, "_extract_chunk_uncached", produce)
    await extractor._extract_chunk("A", [FieldDef("title")], 100)
    def deny(*args, **kwargs):
        raise PermissionError("privacy revoked")
    monkeypatch.setattr("omnicrawler.core.ai_env.require_ai_privacy", deny)
    with pytest.raises(PermissionError):
        await extractor._extract_chunk("A", [FieldDef("title")], 100)
