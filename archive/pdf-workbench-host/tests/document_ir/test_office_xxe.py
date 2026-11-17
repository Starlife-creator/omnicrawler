"""B-P3-12：ODT/EPUB 解析统一 defusedxml 口径（XXE/DTD 拒绝）；B-P2-14c：ODT 递归深度上限。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from defusedxml.common import DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden

from omnicrawler.document_ir import parse_document

_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def _write_odt(path: Path, content_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content_xml)


def test_odt_rejects_external_entity(tmp_path: Path) -> None:
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE office:document [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>'
        f'<office:document xmlns:office="{_OFFICE_NS}" xmlns:text="{_TEXT_NS}">'
        "<text:p>&xxe;</text:p></office:document>"
    )
    target = tmp_path / "evil.odt"
    _write_odt(target, payload)

    with pytest.raises((DTDForbidden, EntitiesForbidden, ExternalReferenceForbidden)):
        parse_document(target)


def test_odt_recursion_depth_capped_without_recursion_error(tmp_path: Path) -> None:
    inner = "<text:p>leaf</text:p>"
    for _ in range(60):
        inner = f"<wrap>{inner}</wrap>"
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document xmlns:office="{_OFFICE_NS}" xmlns:text="{_TEXT_NS}">{inner}'
        "</office:document>"
    )
    target = tmp_path / "deep.odt"
    _write_odt(target, payload)

    doc = parse_document(target)  # 不得抛 RecursionError
    assert doc.warnings, "超过深度上限应记录告警"
    assert any("深度上限" in warning for warning in doc.warnings)
