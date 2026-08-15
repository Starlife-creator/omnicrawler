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
    # B05-019：默认 fail-closed，需显式开启 allow_pdf_content 才允许 PDF 正文外发
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "ai_config.json").write_text(
        '{"privacy": {"allow_pdf_content": true}}', encoding="utf-8"
    )
    client = OpenAICompatibleClient(
        {
            "api_key": "secret",
            "base_url": "https://api.example.com/v1",
            "model": "test-model",
            "retry_attempts": 1,
        },
        workspace=work,
    )

    assert client.extract("only use supplied evidence") == {"document_type": None, "records": []}
    assert seen["endpoint"] == "https://api.example.com/v1/chat/completions"
    assert seen["workspace"] == work.resolve()
    assert seen["purpose"] == "ai"
    assert seen["method"] == "POST"
    assert seen["max_response_bytes"] == 10 * 1024 * 1024


def test_pdf_content_externalization_blocked_by_default(monkeypatch, tmp_path: Path) -> None:
    """B05-019：未显式开启 allow_pdf_content 时，PDF 正文外发被拒（fail-closed）。"""
    import pytest

    monkeypatch.setattr("omnicrawl.pdfx.llm.scoped_json_request", lambda *a, **k: {})
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    client = OpenAICompatibleClient(
        {"api_key": "secret", "base_url": "https://api.example.com/v1",
         "model": "test-model", "retry_attempts": 1},
        workspace=work,
    )
    with pytest.raises(RuntimeError, match="allow_pdf_content"):
        client.extract("only use supplied evidence")
