from __future__ import annotations

from omnicrawl.fetching.browser_fetcher import strip_cross_origin_credentials


def test_same_origin_keeps_credentials() -> None:
    headers = {"authorization": "Bearer abc", "accept": "application/json"}
    result = strip_cross_origin_credentials(
        headers, "https://api.example.org/v1/items", "https://api.example.org/v1/items?page=2"
    )
    assert result is None


def test_cross_origin_strips_auth_credentials() -> None:
    """S1.3.4：跨来源请求（CDN/三方脚本）不得携带认证凭据头。"""
    headers = {
        "authorization": "Bearer abc",
        "cookie": "session=xyz",
        "x-api-key": "AKIA123",
        "accept": "text/html",
        "x-requested-with": "fetch",
    }
    stripped = strip_cross_origin_credentials(headers, "https://api.example.org/", "https://cdn.example.net/script.js")
    assert stripped is not None
    assert "authorization" not in stripped
    assert "cookie" not in stripped
    assert "x-api-key" not in stripped
    assert stripped["accept"] == "text/html"
    assert stripped["x-requested-with"] == "fetch"


def test_cross_origin_no_credentials_returns_none() -> None:
    headers = {"accept": "application/json", "user-agent": "omnicrawl"}
    result = strip_cross_origin_credentials(headers, "https://a.example.org/", "https://cdn.example.net/x.js")
    assert result is None


def test_missing_target_falls_back_to_stripping() -> None:
    headers = {"authorization": "Bearer abc", "accept": "*/*"}
    stripped = strip_cross_origin_credentials(headers, "", "https://other.example.net/x")
    assert stripped is not None
    assert "authorization" not in stripped
