from omnicrawler.pdfx.ocr import _paddle_page_result


def test_paddle_rich_result_preserves_boxes_blocks_and_table_spans():
    result = _paddle_page_result(
        {
            "overall_ocr_res": {
                "rec_texts": ["Title"],
                "rec_boxes": [[1, 2, 30, 12]],
                "rec_scores": [0.42],
            },
            "parsing_res_list": [{
                "block_label": "text", "block_content": "Title", "block_bbox": [0, 0, 50, 20],
            }],
            "table_res_list": [{
                "pred_html": '<table><tr><th colspan="2">Revenue</th></tr></table>',
            }],
        },
        {"markdown_texts": "Title"},
    )
    assert result.text.startswith("Title")
    assert result.confidence == 0.42
    assert result.words[0]["bbox"] == [1.0, 2.0, 30.0, 12.0]
    assert result.blocks[0]["bbox"] == [0.0, 0.0, 50.0, 20.0]
    assert result.tables[0]["cells"][0]["column_span"] == 2


def test_malformed_paddle_score_keeps_rich_geometry_but_withholds_confidence():
    result = _paddle_page_result(
        {"overall_ocr_res": {"rec_texts": ["A"], "rec_boxes": [[0, 0, 1, 1]], "rec_scores": ["bad"]}},
        {"markdown_texts": "A"},
    )
    assert result.text == "A"
    assert result.confidence is None
    assert result.words[0]["bbox"] == [0.0, 0.0, 1.0, 1.0]
