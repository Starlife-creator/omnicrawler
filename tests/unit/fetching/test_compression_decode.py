"""S2.5.6：br/zstd 压缩解码补全（或显式声明不支持）。"""

from __future__ import annotations

import pytest

from omnicrawler.core.errors import ResponseTooLargeError
from omnicrawler.fetching.http_client import HTTPFetcher


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


def test_br_bomb_bounded_not_full_decompressed() -> None:
    """FINAL-S2：br 解压必须限流——超限立即中止而非全量解压后事后检查。

    构造高膨胀比炸弹（小压缩体 → 大解压体），限流阈值远小于解压总量；
    若实现仍是先全量 decompress 再查长度，本测试的内存曲线会暴露问题，
    这里以正确性断言兜底：结果必须是 ResponseTooLargeError 且不产出明文。
    """
    try:
        import brotli
    except ImportError:
        pytest.skip("brotli not installed")
    bomb_plain = b"A" * 5_000_000  # 5MB 明文
    encoded = brotli.compress(bomb_plain)
    assert len(encoded) < len(bomb_plain) // 10  # 确认是有效炸弹（高膨胀比）
    with pytest.raises(ResponseTooLargeError):
        HTTPFetcher._decode_br(encoded, 64 * 1024)


def test_unknown_encoding_passthrough() -> None:
    assert HTTPFetcher._decode_content(b"raw", "identity", 100) == b"raw"
