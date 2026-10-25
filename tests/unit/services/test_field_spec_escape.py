"""B-P2-19：field_spec 帮助 HTML 全插值转义（纵深防御 XSS 负样例）。"""

from __future__ import annotations

from omnicrawler.services import field_spec
from omnicrawler.services.field_spec import FieldSpec, field_help_html


def _malicious_spec() -> FieldSpec:
    payload = '<script>alert("xss")</script>'
    return FieldSpec(
        "x",
        payload,
        payload,
        payload,
        payload,
        payload,
        payload,
        payload,
        payload,
        technical_name=payload,
    )


def test_help_html_escapes_all_interpolations(monkeypatch) -> None:
    monkeypatch.setitem(field_spec.FIELD_SPECS, "x", _malicious_spec())

    html = field_help_html("x", mode="developer")

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_unknown_field_id_escaped() -> None:
    html = field_help_html("<img src=x onerror=alert(1)>")
    assert "<img" not in html
    assert "&lt;img" in html
