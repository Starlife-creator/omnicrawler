from __future__ import annotations

from omnicrawl.quality.diagnostics import AutoFix, DiagnoseReport


def test_html_card_escapes_untrusted_error_and_fix_text() -> None:
    report = DiagnoseReport(
        user_facing="server said <img src=x onerror=alert(1)>",
        auto_fix=AutoFix(description='use <script>alert(1)</script>', command="ignored"),
    )

    card = report.to_html_card()

    assert "<img src=x" not in card
    assert "<script>" not in card
    assert "&lt;img src=x onerror=alert(1)&gt;" in card
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in card
