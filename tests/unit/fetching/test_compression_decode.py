"""S2.5.6：br/zstd 压缩解码补全（或显式声明不支持）。"""

from __future__ import annotations

import pytest

from omnicrawl.core.errors import ResponseTooLargeError
from omnicrawl.fetching.http_client import HTTPFetcher


def test_br_roundtrip(monkeypatch) -> None:
    try:
        import brotli
    except ImportError:
        pytest.skip("brotli not installed")
    payload = b"<html>br content</html>" * 100
    encoded = brotli.compress(payload)
    assert HTTPFetcher._decode_content(encoded, "br", 10_000_000) == payload


def test_br_missing_package_raises_visible_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "brotli":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(ValueError, match="brotli"):
        HTTPFetcher._decode_content(b"\x00\x01", "br", 1024)


def test_zstd_roundtrip(monkeypatch) -> None:
    try:
        import zstandard
    except ImportError:
        pytest.skip("zstandard not installed")
    payload = b"<html>zstd content</html>" * 100
    encoded = zstandard.ZstdCompressor().compress(payload)
    assert HTTPFetcher._decode_content(encoded, "zstd", 10_000_000) == payload


def test_zstd_missing_package_raises_visible_error(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "zstandard":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(ValueError, match="zstandard"):
        HTTPFetcher._decode_content(b"\x28\xb5\x2f\xfd", "zstd", 1024)


def test_br_oversize_rejected() -> None:
    try:
        import brotli
    except ImportError:
        pytest.skip("brotli not installed")
    encoded = brotli.compress(b"x" * 2000)
    with pytest.raises(ResponseTooLargeError):
        HTTPFetcher._decode_content(encoded, "br", 100)


def test_unknown_encoding_passthrough() -> None:
    assert HTTPFetcher._decode_content(b"raw", "identity", 100) == b"raw"
