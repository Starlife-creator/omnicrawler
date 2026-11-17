"""B-P2-17：段落区域匹配改首词倒排定位（行为保持）；失败记录含截断异常消息。"""

from __future__ import annotations

from omnicrawler.document_ir.pdf import _paragraph_region


def test_region_union_of_word_bboxes() -> None:
    words = [
        {"text": "Recovered", "bbox": [0.0, 0.0, 10.0, 10.0]},
        {"text": "scan.", "bbox": [20.0, 2.0, 30.0, 12.0]},
    ]
    region = _paragraph_region("Recovered scan.", words)
    assert region == [0.0, 0.0, 30.0, 12.0]


def test_region_falls_back_to_x0_top_x1_bottom_keys() -> None:
    words = [
        {"text": "Hello", "x0": 1.0, "top": 2.0, "x1": 11.0, "bottom": 12.0},
        {"text": "World", "x0": 21.0, "top": 3.0, "x1": 31.0, "bottom": 13.0},
    ]
    region = _paragraph_region("Hello World", words)
    assert region == [1.0, 2.0, 31.0, 13.0]


def test_no_match_returns_none() -> None:
    words = [{"text": "other", "bbox": [0, 0, 10, 10]}]
    assert _paragraph_region("Missing phrase", words) is None


def test_empty_text_returns_none() -> None:
    assert _paragraph_region("", [{"text": "x", "bbox": [0, 0, 1, 1]}]) is None


def test_region_index_matches_after_nonmatching_prefix_words() -> None:
    # 首词倒排索引定位：前缀若干不相关词后仍能命中目标短语
    words = [
        {"text": "preamble", "bbox": [0, 0, 5, 5]},
        {"text": "prefix", "bbox": [6, 0, 10, 5]},
        {"text": "target", "bbox": [12.0, 1.0, 20.0, 6.0]},
        {"text": "phrase", "bbox": [22.0, 2.0, 30.0, 7.0]},
    ]
    region = _paragraph_region("target phrase", words)
    assert region == [12.0, 1.0, 30.0, 7.0]
