"""Local keyless extraction keeps credential, privacy and transport boundaries."""
import json

import pytest

from omnicrawler.core.ai_env import save_ai_config_sidecar
from omnicrawler.core.errors import AIPrivacyBlockedError
from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, Provider


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:12355/v1", "http://[::1]:12355/v1"])
async def test_explicit_loopback_keyless_extract_omits_authorization(tmp_path, monkeypatch, url):
    save_ai_config_sidecar(tmp_path, {"privacy": {"allow_page_text": True}})
    extractor = AIGraphExtractor(provider=Provider(base_url=url, allow_keyless_local=True), project_root=tmp_path)
    calls = []

    async def post(*args, **kwargs):
        calls.append((args, kwargs))
        return {"choices": [{"message": {"content": json.dumps({"fields": {"id": "A123"}})}}]}

    monkeypatch.setattr(extractor, "_post_with_retry", post)
    result = await extractor._extract_chunk("<p>A123</p>", [FieldDef("id")], 256, session=object())
    assert result["fields"] == {"id": "A123"}
    assert len(calls) == 1
    assert calls[0][0][1] == url + "/chat/completions"
    assert calls[0][1]["headers"] == {"Content-Type": "application/json"}


@pytest.mark.asyncio
@pytest.mark.parametrize("url,enabled", [
    ("http://127.0.0.1:12355/v1", False),
    ("http://127.0.0.1:12355/v1", "true"),
    ("https://api.example.com/v1", True),
    ("http://192.168.1.10/v1", True),
    ("http://localhost/v1", True),
    ("http://127.0.0.1.example.com/v1", True),
    ("http://user:password@127.0.0.1/v1", True),
    ("file://127.0.0.1/v1", True),
    ("http://127.0.0.1:invalid/v1", True),
    ("http://127.0.0.1/v1?redirect=remote", True),
    ("http://127.0.0.1/v1#fragment", True),
])
async def test_keyless_cloud_alias_and_implicit_optin_fail_before_transport(tmp_path, monkeypatch, url, enabled):
    save_ai_config_sidecar(tmp_path, {"privacy": {"allow_page_text": True}})
    extractor = AIGraphExtractor(provider=Provider(base_url=url, allow_keyless_local=enabled), project_root=tmp_path)

    async def forbidden(*args, **kwargs):
        pytest.fail("credential boundary must reject before transport")

    monkeypatch.setattr(extractor, "_post_with_retry", forbidden)
    with pytest.raises(RuntimeError, match="API key"):
        await extractor._extract_chunk("<p>A123</p>", [FieldDef("id")], 256, session=object())


@pytest.mark.asyncio
async def test_local_keyless_does_not_bypass_privacy_or_missing_egress(tmp_path):
    extractor = AIGraphExtractor(provider=Provider(base_url="http://127.0.0.1:12355/v1", allow_keyless_local=True),
                                 project_root=tmp_path)
    with pytest.raises(AIPrivacyBlockedError):
        await extractor._extract_chunk("<p>A123</p>", [FieldDef("id")], 256, session=object())
    save_ai_config_sidecar(tmp_path, {"privacy": {"allow_page_text": True}})
    with pytest.raises(RuntimeError, match="EgressBroker"):
        await extractor._extract_chunk("<p>A123</p>", [FieldDef("id")], 256, session=object())
