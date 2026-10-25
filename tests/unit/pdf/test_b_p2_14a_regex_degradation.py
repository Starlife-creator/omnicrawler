"""B-P2-14a：可选 ``regex`` 缺失回退标准库 re 时，降级告警只发一次。"""

from __future__ import annotations

import logging

import omnicrawler.pdfx.safe_regex as safe_regex


def test_fallback_without_regex_warns_once(
    caplog: logging.Logger,
    monkeypatch,
) -> None:
    monkeypatch.setattr(safe_regex, "timeout_regex", None)
    monkeypatch.setattr(safe_regex, "_degradation_warned", False)
    safe_regex._compile_pattern.cache_clear()

    with caplog.at_level(logging.WARNING, logger=safe_regex.LOGGER.name):
        safe_regex.search(r"abc", "abc")
        safe_regex.findall_count(r"abc", "abc")

    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING and "标准库" in record.message
    ]
    # 两条调用路径（search / findall_count）只产生一次降级告警，避免刷屏。
    assert len(warnings) == 1
