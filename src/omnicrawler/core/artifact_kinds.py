"""由抓取结果推断 artifact 的落盘类别（纯函数，无 IO、不读配置）。

AGENTS.md 原则 3：站点与格式差异配置化，不要把站点特例写进 Pipeline 编排层。
PDF / 媒体 / 普通文件的判定原先内联在 ``pipeline/_fetch.py`` 的 ``_save_artifact``
里——那属于「由内容推断格式」的通用职责，与编排无关，因此收在这里：可单测、
可复用，编排层只负责落盘。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PDF_CONTENT_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF-"
_MEDIA_PREFIXES = ("image/", "audio/", "video/")

CATEGORY_PDF = "pdf"
CATEGORY_MEDIA = "media"
CATEGORY_FILES = "files"


@dataclass(frozen=True, slots=True)
class ArtifactKind:
    """一个 artifact 的落盘类别与写盘用的 Content-Type。"""

    category: str
    content_type: str


def url_suffix(final_url: str) -> str:
    """URL 路径部分的扩展名（小写），忽略 query 与 fragment。"""
    path = final_url.split("?", 1)[0].split("#", 1)[0]
    return Path(path).suffix.lower()


def looks_like_pdf(*, content_type: str, final_url: str, body: bytes) -> bool:
    """三个信号任一命中即视为 PDF：声明类型、URL 后缀、文件魔数。"""
    if content_type == PDF_CONTENT_TYPE:
        return True
    if url_suffix(final_url) == ".pdf":
        return True
    return body.startswith(PDF_MAGIC)


def classify_artifact(*, content_type: str, final_url: str, body: bytes) -> ArtifactKind:
    """把一次抓取结果映射为 (类别, 写盘 Content-Type)。

    服务端可能把 PDF 标成 ``application/octet-stream``，也可能给错误的 URL 后缀，
    所以三个信号都要看；一旦判定为 PDF，写盘类型统一为 ``application/pdf``，
    否则下游按 content-type 找 PDF 的逻辑会漏。
    """
    if looks_like_pdf(content_type=content_type, final_url=final_url, body=body):
        return ArtifactKind(category=CATEGORY_PDF, content_type=PDF_CONTENT_TYPE)
    if content_type.startswith(_MEDIA_PREFIXES):
        return ArtifactKind(category=CATEGORY_MEDIA, content_type=content_type)
    return ArtifactKind(category=CATEGORY_FILES, content_type=content_type)
