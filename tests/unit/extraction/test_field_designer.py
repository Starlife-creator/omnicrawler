import unittest

from omnicrawl.extraction.field_designer import analyze_html
from omnicrawl.fetching.action_recorder import ActionSequence


class FieldDesignerTest(unittest.TestCase):
    def test_ranks_stable_business_fields(self):
        html = """
        <article data-testid="product-card">
          <h1 id="product-title">研究用显微镜</h1>
          <span class="price">¥12,800</span>
          <time datetime="2026-07-18">今天</time>
          <a itemprop="url" href="/item/1">详情</a>
        </article>
        """
        candidates = analyze_html(html)
        by_name = {item.suggested_name: item for item in candidates}
        self.assertEqual(by_name["title"].css, "#product-title")
        self.assertEqual(by_name["price"].css, "span.price")
        self.assertEqual(by_name["date"].attribute, "datetime")
        self.assertTrue(any(item.attribute == "href" for item in candidates))

    def test_action_sequence_redacts_password_and_compacts_fill(self):
        sequence = ActionSequence()
        sequence.add_event({"type": "change", "selector": "#query", "value": "a"})
        sequence.add_event({"type": "change", "selector": "#query", "value": "abc"})
        sequence.add_event({"type": "change", "selector": "#password", "value": "secret", "secret": True})
        sequence.add_event({"type": "keydown", "selector": "#password", "key": "Enter"})
        actions = sequence.to_config()
        self.assertEqual(actions[0]["value"], "abc")
        self.assertEqual(actions[1]["value"], "secret://browser_password")
        self.assertEqual(actions[2]["action"], "press")
