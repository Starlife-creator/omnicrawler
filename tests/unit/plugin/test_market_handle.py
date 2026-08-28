from __future__ import annotations

import sys
from pathlib import Path

import pytest

MARKET_TOOLS = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "tools"
if str(MARKET_TOOLS) not in sys.path:
    sys.path.insert(0, str(MARKET_TOOLS))

from catalog_lib.authors import assign_market_handle  # noqa: E402


def test_market_handle_is_key_stable_and_duplicate_names_get_suffixes() -> None:
    authors = {
        "alice": {"fingerprint": "a" * 32, "_fingerprint": "a" * 32},
        "alice-01": {"fingerprint": "b" * 32, "_fingerprint": "b" * 32},
    }
    assert assign_market_handle(authors, "a" * 32, "someone-else") == "alice"
    assert assign_market_handle(authors, "c" * 32, "Alice") == "alice-02"


def test_market_handle_rejects_unicode_and_unsafe_names() -> None:
    for value in ("张三", "Alice Smith", "a", "../alice"):
        with pytest.raises(ValueError):
            assign_market_handle({}, "a" * 32, value)
