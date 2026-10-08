import pytest

from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, SplitStrategy


def test_long_sections_repeat_unit_and_table_header_with_bounded_input():
    extractor = AIGraphExtractor(chunk_size=500)
    rows = "".join(f"<tr><td>entity-{index}</td><td>{index * 100}</td></tr>" for index in range(30))
    html = '<h1>Amounts in USD</h1><table><tr><th>Entity</th><th>Amount</th></tr>' + rows + '</table>'
    chunks = extractor._split_html(html, SplitStrategy.HEADING)
    assert len(chunks) > 1 and all(len(chunk) <= 500 for chunk in chunks)
    assert all("Amounts in USD" in chunk and "<th>Amount</th>" in chunk for chunk in chunks)
    assert all(any(f"entity-{index}</td>" in chunk for chunk in chunks) for index in range(30))


def test_multiple_tables_keep_their_own_headers_and_units():
    extractor = AIGraphExtractor(chunk_size=500)
    tables = []
    for label, unit in (("expense", "USD"), ("revenue", "CNY")):
        rows = "".join(f"<tr><td>{label}-{index}</td><td>{index}</td></tr>" for index in range(20))
        tables.append(f"<table><caption>Units {unit}</caption><tr><th>{label}</th><th>Amount</th></tr>{rows}</table>")
    chunks = extractor._split_html("<h1>Annual report</h1>" + "".join(tables), SplitStrategy.HEADING)
    assert all(len(chunk) <= 500 for chunk in chunks)
    for label, unit, other in (("expense", "USD", "revenue"), ("revenue", "CNY", "expense")):
        relevant = [chunk for chunk in chunks if f"{label}-" in chunk]
        assert relevant and all(f"<th>{label}</th>" in chunk and f"Units {unit}" in chunk for chunk in relevant)
        assert all(f"<th>{other}</th>" not in chunk for chunk in relevant)
        assert all(any(f"{label}-{index}</td>" in chunk for chunk in relevant) for index in range(20))


def test_unsupported_and_conflicting_model_values_retain_review_state_and_locations():
    extractor = AIGraphExtractor()
    first = {"fields": {"amount": 10}, "confidence": 0.99, "evidence": {"amount": {"quote": "Amount 10 USD"}}}
    later = {"fields": {"amount": 90}, "confidence": 0.99, "evidence": {"amount": {"quote": "invented quote 90"}}}
    extractor._ground_fields(first, "<p>Amount 10 USD</p>")
    extractor._ground_fields(later, "<p>Amount 20 USD</p>")
    merged = extractor._merge_results([first, later], 2, fields=[FieldDef("amount", field_type="number")])
    extractor._assess_target(merged, [FieldDef("amount", field_type="number")])
    assert merged["review_required"]
    assert merged["unsupported_fields"] == ["amount"]
    assert merged["grounding"]["amount"][0]["text_offset"] == 0
    assert merged["grounding"]["amount"][1]["status"] == "unsupported"
    assert merged["conflicts"][0]["later"] == 90
    assert merged["confidence_semantics"] == "model_self_report_unvalidated"


@pytest.mark.asyncio
async def test_chunk_budget_rejects_oversized_input_before_model_calls(monkeypatch):
    extractor = AIGraphExtractor(chunk_size=500, max_chunks=1)
    async def forbidden(*args, **kwargs):
        pytest.fail("over-budget input contacted the model")
    monkeypatch.setattr(extractor, "_extract_chunk", forbidden)
    with pytest.raises(ValueError, match="max_chunks"):
        await extractor.extract("x" * 1200, [FieldDef("title")])
