import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.quality.schema_registry import field_contract_schema, validate_target_fields
from omnicrawler.services.ai_providers import OpenAICompatibleProvider


@pytest.mark.parametrize("supported", [False, True])
def test_native_schema_is_sent_only_for_declared_capability(tmp_path, monkeypatch, supported):
    captured = []
    provider = OpenAICompatibleProvider("local", {
        "base_url": "http://localhost:1234/v1", "model": "test", "supports_json_schema": supported,
    }, app_config=AppConfig(tmp_path / "task.yaml", tmp_path, {"http": {}}, tmp_path, ()),
       egress=SimpleNamespace(policy=None, request=lambda *a, **k: nullcontext(), record_response=lambda *a, **k: None))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, maximum):
            return b'{"choices":[{"message":{"content":"{}"}}],"usage":{}}'

        def geturl(self):
            return "http://localhost:1234/v1/chat/completions"

    def open_request(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("omnicrawler.services.ai_providers.build_safe_opener",
                        lambda *args, **kwargs: SimpleNamespace(open=open_request))
    schema = field_contract_schema({"price": {"type": "number", "min": 0}})
    provider.generate([{"role": "user", "content": "fixture"}], response_schema=schema, schema_name="fixture")
    assert len(captured) == 1
    if supported:
        assert captured[0]["response_format"]["json_schema"]["schema"] == schema
        assert captured[0]["response_format"]["json_schema"]["strict"] is False
    else:
        assert "response_format" not in captured[0]


def test_nested_schema_preserves_optional_and_nullable_contract():
    rules = {"price": {"type": "number", "required": True, "min": 0},
             "stock": {"type": "boolean", "nullable": False},
             "rows": {"type": "array", "items": {"type": "object", "properties": {
                 "id": {"type": "integer", "required": True},
                 "label": {"type": "string", "nullable": True},
             }}}}
    schema = field_contract_schema(rules)
    assert schema["required"] == ["price"]
    assert schema["properties"]["stock"]["type"] == "boolean"
    row = schema["properties"]["rows"]["items"]
    assert row["required"] == ["id"]
    assert row["properties"]["label"]["type"] == ["string", "null"]
    assert field_contract_schema(rules, enforce_required=False)["required"] == []
    assert not validate_target_fields({"price": 0, "stock": False, "rows": [{"id": 0}]}, rules)
    with pytest.raises(ValueError, match="类型错误"):
        validate_target_fields({"price": False}, rules)


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [False, True])
async def test_graph_native_guidance_keeps_zero_false_and_local_validation(monkeypatch, supported):
    from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, Provider
    monkeypatch.setattr("omnicrawler.core.ai_env.require_ai_privacy", lambda *a, **k: None)
    extractor = AIGraphExtractor(provider=Provider(api_key="fixture", supports_json_schema=supported))
    captured = []
    values = {"price": 0, "stock": False}

    async def post(*args, **kwargs):
        captured.append(kwargs["payload"])
        return {"choices": [{"message": {"content": json.dumps({"fields": values, "confidence": 0.9})}}]}

    monkeypatch.setattr(extractor, "_post_with_retry", post)
    fields = [FieldDef("price", field_type="number", required=True), FieldDef("stock", field_type="boolean")]
    result = await extractor._extract_chunk("fixture", fields, 256, session=object())
    assert result["fields"] == values
    if supported:
        schema = captured[0]["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["fields"]["properties"]["price"]["type"] == "number"
        assert schema["properties"]["fields"]["required"] == []  # A chunk can omit a required field.
    else:
        assert "response_format" not in captured[0]
    values["price"] = False
    with pytest.raises(ValueError, match="类型错误"):
        await extractor._extract_chunk("fixture", fields, 256, session=object())
