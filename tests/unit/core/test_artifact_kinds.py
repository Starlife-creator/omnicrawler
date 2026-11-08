"""artifact 类别判定的契约测试。

这条判定的价值在于「服务端说错、URL 骗人、正文才是真的」三种情况都要兜住——
所以三个信号各有一条正向断言，两条负向断言保证不会把普通文件误判成 PDF。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.artifact_kinds import (
    CATEGORY_FILES,
    CATEGORY_MEDIA,
    CATEGORY_PDF,
    PDF_CONTENT_TYPE,
    classify_artifact,
    looks_like_pdf,
    url_suffix,
)

_PDF_BODY = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"


def test_pdf_content_type_is_enough() -> None:
    kind = classify_artifact(content_type="application/pdf", final_url="https://x/a.bin", body=b"")
    assert kind.category == CATEGORY_PDF
    assert kind.content_type == PDF_CONTENT_TYPE


def test_pdf_suffix_is_enough_when_server_lies_about_type() -> None:
    kind = classify_artifact(
        content_type="application/octet-stream",
        final_url="https://x/paper.PDF",
        body=b"\x00\x01\x02",
    )
    assert kind.category == CATEGORY_PDF
    assert kind.content_type == PDF_CONTENT_TYPE


def test_pdf_magic_is_enough_when_type_and_suffix_are_wrong() -> None:
    kind = classify_artifact(
        content_type="text/plain",
        final_url="https://x/download",
        body=_PDF_BODY,
    )
    assert kind.category == CATEGORY_PDF
    assert kind.content_type == PDF_CONTENT_TYPE


@pytest.mark.parametrize("content_type", ["image/png", "audio/mpeg", "video/mp4"])
def test_media_types_map_to_media_and_keep_their_type(content_type: str) -> None:
    kind = classify_artifact(content_type=content_type, final_url="https://x/blob", body=b"\x89PNG")
    assert kind.category == CATEGORY_MEDIA
    assert kind.content_type == content_type


def test_unknown_binary_falls_back_to_files_without_rewriting_the_type() -> None:
    kind = classify_artifact(
        content_type="application/zip",
        final_url="https://x/archive.zip",
        body=b"PK\x03\x04",
    )
    assert kind.category == CATEGORY_FILES
    assert kind.content_type == "application/zip"


def test_pdf_magic_must_be_a_prefix_not_a_substring() -> None:
    """反向断言：正文中间出现 ``%PDF-`` 不能算 PDF，否则任意二进制都会被当成 PDF。"""
    kind = classify_artifact(
        content_type="application/octet-stream",
        final_url="https://x/blob",
        body=b"junk" + _PDF_BODY,
    )
    assert kind.category == CATEGORY_FILES
    assert looks_like_pdf(content_type="application/octet-stream", final_url="https://x/blob", body=b"junk%PDF-") is False


def test_url_suffix_ignores_query_and_fragment() -> None:
    assert url_suffix("https://x/a.pdf?v=2") == ".pdf"
    assert url_suffix("https://x/a.pdf#page=3") == ".pdf"
    assert url_suffix("https://x/a?file=b.pdf") == ""
