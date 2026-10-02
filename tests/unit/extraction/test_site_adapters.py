from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import FetchResult
from omnicrawler.sources.site_adapters import MediaWikiSource, WordPressSource


def _config(tmp_path: Path, kind: str, seed: str, **source_values):
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "adapter", "workspace": str(tmp_path / "work")},
        "source": {"kind": kind, "seeds": [seed], **source_values},
        "http": {"user_agent": "AdapterTest/1.0 (+contact: test@example.org)"},
    }), encoding="utf-8")
    return load_config(path)


def test_wordpress_uses_total_pages_response_header(tmp_path: Path) -> None:
    source = WordPressSource(_config(
        tmp_path,
        "site_wordpress",
        "https://example.org/wp-json/wp/v2/posts?per_page=100",
        max_pages=5,
    ))
    request = source.seed()[0]
    result = FetchResult(request, request.url, 200, {"x-wp-totalpages": "3"}, b"[]", 0.01)

    discovered = source.discover(result)

    assert len(discovered) == 1
    assert "page=2" in discovered[0].url
    assert discovered[0].meta["page"] == 2


def test_mediawiki_copies_all_continuation_parameters(tmp_path: Path) -> None:
    source = MediaWikiSource(_config(
        tmp_path,
        "site_mediawiki",
        "https://example.org/w/api.php?action=query&list=categorymembers",
    ))
    request = source.seed()[0]
    body = json.dumps({"continue": {"continue": "-||", "cmcontinue": "page|123"}}).encode()
    result = FetchResult(request, request.url, 200, {"content-type": "application/json"}, body, 0.01)

    discovered = source.discover(result)

    assert len(discovered) == 1
    assert "cmcontinue=page%7C123" in discovered[0].url
    assert "continue=-%7C%7C" in discovered[0].url


def test_wordpress_replaces_page_on_every_hop_and_preserves_filters(tmp_path: Path) -> None:
    source = WordPressSource(_config(tmp_path, "site_wordpress",
                                    "https://example.org/posts?tag=a&tag=b&empty=&page=1"))
    request = source.seed()[0]
    for page in (2, 3):
        result = FetchResult(request, request.url, 200, {"x-wp-totalpages": "3"}, b"[]", .01)
        request = source.discover(result)[0]
        assert parse_qs(urlsplit(request.url).query, keep_blank_values=True) == {
            "tag": ["a", "b"], "empty": [""], "page": [str(page)]}
        assert request.depth == page - 1
    result = FetchResult(request, request.url, 200, {"x-wp-totalpages": "3"}, b"[]", .01)
    assert source.discover(result) == []


def test_mediawiki_replaces_tokens_and_stops_repeated_continuation(tmp_path: Path) -> None:
    source = MediaWikiSource(_config(tmp_path, "site_mediawiki",
                                    "https://example.org/api?tag=a&tag=b&cmcontinue=old"))
    request = source.seed()[0]
    for token in ("next", "last"):
        body = json.dumps({"continue": {"continue": "-||", "cmcontinue": token}}).encode()
        result = FetchResult(request, request.url, 200, {}, body, .01)
        request = source.discover(result)[0]
        assert parse_qs(urlsplit(request.url).query) == {
            "tag": ["a", "b"], "continue": ["-||"], "cmcontinue": [token]}
    with pytest.raises(ValueError, match="collection is incomplete"):
        source.discover(FetchResult(request, request.url, 200, {}, body, .01))


def test_mediawiki_token_key_transition_drops_only_old_continuation(tmp_path: Path) -> None:
    source = MediaWikiSource(_config(tmp_path, "site_mediawiki", "https://example.org/api?tag=a&tag=b"))
    request = source.seed()[0]
    for continuation in ({"cmcontinue": "old", "continue": "-||"}, {"apcontinue": "next"}):
        request = source.discover(FetchResult(request, request.url, 200, {},
                                            json.dumps({"continue": continuation}).encode(), .01))[0]
    assert parse_qs(urlsplit(request.url).query) == {"tag": ["a", "b"], "apcontinue": ["next"]}


def test_mediawiki_continuation_cycle_stops(tmp_path: Path) -> None:
    source = MediaWikiSource(_config(tmp_path, "site_mediawiki", "https://example.org/api?next=1"))
    request = source.seed()[0]
    result = FetchResult(request, request.url, 200, {}, b'{"continue":{"next":2}}', .01)
    request = source.discover(result)[0]
    result = FetchResult(request, request.url, 200, {}, b'{"continue":{"next":1}}', .01)
    with pytest.raises(ValueError, match="collection is incomplete"):
        source.discover(result)
