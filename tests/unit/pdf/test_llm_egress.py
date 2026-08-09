from __future__ import annotations

from pathlib import Path

from omnicrawl.pdfx.llm import OpenAICompatibleClient


def test_openai_compatible_client_uses_scoped_egress_request(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_request(endpoint: str, **kwargs):
        seen["endpoint"] = endpoint
        seen.update(kwargs)
        return {
            "choices": [
                {"message": {"content": '{"document_type": null, "records": []}'}}
            ]
        }

    monkeypatch.setattr("omnicrawl.pdfx.llm.scoped_json_request", fake_request)
    client = OpenAICompatibleClient(
        {
            "api_key": "secret",
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
            "retry_attempts": 1,
        },
        workspace=tmp_path / "work",
    )

    assert client.extract("only use supplied evidence") == {"document_type": None, "records": []}
    assert seen["endpoint"] == "https://api.example.com/v1/chat/completions"
    assert seen["workspace"] == (tmp_path / "work").resolve()
    assert seen["purpose"] == "ai"
    assert seen["method"] == "POST"
    assert seen["max_response_bytes"] == 10 * 1024 * 1024
