from __future__ import annotations

import json
from pathlib import Path

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
