"""S3：URL 脏数据清洗（借鉴 ahmadsalamifar normalize_url 思路重写）。"""

from __future__ import annotations

from omnicrawler.sources.url_cleaner import clean_url, clean_url_status, extract_urls_from_text


# ── clean_url 基础 ───────────────────────────────────────
def test_clean_url_plain() -> None:
    assert clean_url("https://example.com/a/b") == "https://example.com/a/b"


def test_clean_url_http() -> None:
    assert clean_url("http://example.com/page") == "http://example.com/page"


def test_clean_url_strips_whitespace() -> None:
    assert clean_url("  https://example.com/a  ") == "https://example.com/a"


# ── 脏数据场景（借鉴 normalize_url 覆盖点）────────────────
def test_clean_url_csv_adjacent() -> None:
    # CSV 分隔符粘连：取 URL 到逗号为止
    assert clean_url("https://example.com/a,b,item2") == "https://example.com/a"


def test_clean_url_tab_adjacent() -> None:
    assert clean_url("https://example.com/a\t商品名") == "https://example.com/a"


def test_clean_url_pipe_adjacent() -> None:
    assert clean_url("https://example.com/a|B") == "https://example.com/a"


def test_clean_url_parenthesized() -> None:
    assert clean_url("(https://example.com/a)") == "https://example.com/a"


def test_clean_url_quoted() -> None:
    assert clean_url('"https://example.com/a"') == "https://example.com/a"
    assert clean_url("'https://example.com/a'") == "https://example.com/a"


def test_clean_url_angle_bracket() -> None:
    assert clean_url("<https://example.com/a>") == "https://example.com/a"


def test_clean_url_trailing_punctuation() -> None:
    assert clean_url("https://example.com/a。") == "https://example.com/a"
    assert clean_url("https://example.com/a;") == "https://example.com/a"
    assert clean_url("https://example.com/a：") == "https://example.com/a"


def test_clean_url_invisible_chars() -> None:
    assert clean_url("https://example.com/a\u200b") == "https://example.com/a"


# ── 拒绝场景 ─────────────────────────────────────────────
def test_clean_url_rejects_no_scheme() -> None:
    assert clean_url("example.com/a") is None


def test_clean_url_rejects_javascript() -> None:
    assert clean_url("javascript:void(0)") is None


def test_clean_url_rejects_empty() -> None:
    assert clean_url("") is None
    assert clean_url(None) is None


# ── clean_url_status（状态化清洗，消除静默丢弃）──────────
def test_clean_url_status_ok() -> None:
    assert clean_url_status("https://a.com/x") == ("https://a.com/x", "ok")


def test_clean_url_status_ftp_unsupported() -> None:
    assert clean_url_status("ftp://a.com") == (None, "unsupported:ftp")


def test_clean_url_status_javascript_unsupported() -> None:
    # 只有冒号无 ://，urlparse 也能识别协议
    assert clean_url_status("javascript:void(0)") == (None, "unsupported:javascript")


def test_clean_url_status_mailto_unsupported() -> None:
    assert clean_url_status("mailto:x@a.com") == (None, "unsupported:mailto")


def test_clean_url_status_no_scheme() -> None:
    assert clean_url_status("example.com/path") == (None, "no_scheme")


def test_clean_url_status_invalid() -> None:
    assert clean_url_status("") == (None, "invalid")
    assert clean_url_status(None) == (None, "invalid")


# ── extract_urls_from_text ───────────────────────────────
def test_extract_multiple_urls_dedupe() -> None:
    text = "看这里 https://a.com/x 和 https://a.com/x 以及 http://b.com/y"
    urls = extract_urls_from_text(text)
    assert urls == ["https://a.com/x", "http://b.com/y"]


def test_extract_from_wechat_style_text() -> None:
    text = "分享: https://example.com/news,120 千万别错过"
    urls = extract_urls_from_text(text)
    assert urls == ["https://example.com/news"]


def test_extract_empty() -> None:
    assert extract_urls_from_text("") == []
    assert extract_urls_from_text(None) == []
