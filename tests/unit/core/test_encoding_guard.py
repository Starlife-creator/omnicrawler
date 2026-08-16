"""P9-A4（B05-023）：编码白名单收窄测试。"""

from __future__ import annotations

from omnicrawl.core.encoding import _KNOWN_ENCODINGS, detect_encoding, smart_decode


def test_known_encoding_accepted() -> None:
    assert detect_encoding("你好".encode(), fallback="utf-8") == "utf-8"


def test_low_confidence_or_unlisted_falls_back() -> None:
    # 白名单外/低置信编码名（如 chardet 的 cp1250 别名）→ 回退 fallback
    assert detect_encoding(b"", fallback="utf-8") == "utf-8"


def test_encoding_whitelist_contains_common_set() -> None:
    assert {"utf-8", "gb18030", "big5", "shift-jis", "latin-1", "windows-1252"} <= _KNOWN_ENCODINGS


def test_smart_decode_roundtrip() -> None:
    text, enc = smart_decode("你好，世界".encode("gb18030"))
    assert text == "你好，世界"
    assert enc == "gb18030"
