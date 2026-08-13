"""P2-1：结构指纹签名单元测试。

覆盖：type_signature、structure_fingerprint、StructureFingerprintRegistry、
metrics.record_extracted 消费方集成。
"""

from __future__ import annotations


class TestTypeSignature:
    def test_scalars(self) -> None:
        from omnicrawl.core.structure_fingerprint import type_signature

        assert type_signature(None) == "null"
        assert type_signature(True) == "bool"
        assert type_signature(False) == "bool"
        assert type_signature(42) == "int"
        assert type_signature(3.14) == "float"
        assert type_signature("hello") == "str"

    def test_bool_before_int(self) -> None:
        """bool 是 int 子类，必须先判 bool 否则 True → 'int'。"""
        from omnicrawl.core.structure_fingerprint import type_signature

        assert type_signature(True) != "int"

    def test_list(self) -> None:
        from omnicrawl.core.structure_fingerprint import type_signature

        assert type_signature([]) == "[]"
        assert type_signature([1, 2, 3]) == "[int]"
        assert type_signature(["a", "b"]) == "[str]"
        assert type_signature([{"k": 1}]) == "[{k:int}]"

    def test_dict(self) -> None:
        from omnicrawl.core.structure_fingerprint import type_signature

        assert type_signature({}) == "{}"
        assert type_signature({"name": "x"}) == "{name:str}"
        assert type_signature({"name": "x", "age": 30}) == "{age:int,name:str}"

    def test_nested(self) -> None:
        from omnicrawl.core.structure_fingerprint import type_signature

        data = {"tags": ["a"], "meta": {"src": "x", "n": 1}}
        sig = type_signature(data)
        assert "tags:[str]" in sig
        assert "meta:{n:int,src:str}" in sig

    def test_other_types(self) -> None:
        from omnicrawl.core.structure_fingerprint import type_signature

        assert type_signature(b"bytes") == "bytes"
        assert type_signature(object()) == "object"


class TestStructureFingerprint:
    def test_same_structure_different_values_same_fingerprint(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        d1 = {"name": "张三", "age": 30, "tags": ["a"]}
        d2 = {"name": "李四", "age": 25, "tags": ["x", "y"]}
        # 同结构（键相同、类型相同）→ 同指纹，即使值完全不同
        assert structure_fingerprint(d1, record_type="html_item") == structure_fingerprint(
            d2, record_type="html_item"
        )

    def test_different_keys_different_fingerprint(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        d1 = {"name": "x", "age": 30}
        d2 = {"name": "x", "price": 30}
        assert structure_fingerprint(d1) != structure_fingerprint(d2)

    def test_different_value_type_different_fingerprint(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        d1 = {"age": 30}       # int
        d2 = {"age": "30"}     # str
        assert structure_fingerprint(d1) != structure_fingerprint(d2)

    def test_different_record_type_different_fingerprint(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        data = {"name": "x", "age": 30}
        assert structure_fingerprint(data, record_type="html_item") != structure_fingerprint(
            data, record_type="json_item"
        )

    def test_empty_data(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        sig = structure_fingerprint({}, record_type="html_item")
        assert sig == "v1:html_item:empty"

    def test_key_order_independent(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        d1 = {"name": "x", "age": 30}
        d2 = {"age": 30, "name": "x"}
        assert structure_fingerprint(d1) == structure_fingerprint(d2)

    def test_format(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        sig = structure_fingerprint({"name": "x"}, record_type="html_item")
        assert sig.startswith("v1:html_item:")
        assert len(sig.split(":")[-1]) == 16  # 16 hex chars

    def test_nested_dict_in_list(self) -> None:
        from omnicrawl.core.structure_fingerprint import structure_fingerprint

        d1 = {"items": [{"name": "a", "qty": 1}]}
        d2 = {"items": [{"name": "b", "qty": 2}]}
        assert structure_fingerprint(d1) == structure_fingerprint(d2)
        d3 = {"items": [{"name": "a", "price": 1.0}]}
        assert structure_fingerprint(d1) != structure_fingerprint(d3)


class TestStructureFingerprintRegistry:
    def test_observe_and_count(self) -> None:
        from omnicrawl.core.structure_fingerprint import (
            StructureFingerprintRegistry,
            structure_fingerprint,
        )

        reg = StructureFingerprintRegistry()
        sig_a = structure_fingerprint({"name": "x"}, record_type="html_item")
        sig_b = structure_fingerprint({"name": "x", "age": 30}, record_type="html_item")

        reg.observe(sig_a, "https://example.com/1")
        reg.observe(sig_a, "https://example.com/2")
        reg.observe(sig_b, "https://example.com/3")

        assert reg.count(sig_a) == 2
        assert reg.count(sig_b) == 1
        assert reg.unique_count() == 2

    def test_drift_detection(self) -> None:
        from omnicrawl.core.structure_fingerprint import (
            StructureFingerprintRegistry,
            structure_fingerprint,
        )

        reg = StructureFingerprintRegistry()
        sig_a = structure_fingerprint({"name": "x"}, record_type="html_item")
        sig_b = structure_fingerprint({"name": "x", "age": 30}, record_type="html_item")

        # 同 URL 第一次出现 sig_a → 非漂移
        assert reg.observe(sig_a, "https://example.com/page") is False
        # 同 URL 再次出现 sig_a → 非漂移
        assert reg.observe(sig_a, "https://example.com/page") is False
        # 同 URL 出现 sig_b → 漂移！
        assert reg.observe(sig_b, "https://example.com/page") is True

    def test_signatures_for_url(self) -> None:
        from omnicrawl.core.structure_fingerprint import (
            StructureFingerprintRegistry,
            structure_fingerprint,
        )

        reg = StructureFingerprintRegistry()
        sig_a = structure_fingerprint({"name": "x"})
        sig_b = structure_fingerprint({"name": "x", "age": 30})

        reg.observe(sig_a, "https://example.com/1")
        reg.observe(sig_b, "https://example.com/1")

        sigs = reg.signatures_for_url("https://example.com/1")
        assert sig_a in sigs
        assert sig_b in sigs
        assert len(sigs) == 2
        assert len(reg.signatures_for_url("https://nope.com")) == 0

    def test_top_signatures(self) -> None:
        from omnicrawl.core.structure_fingerprint import (
            StructureFingerprintRegistry,
            structure_fingerprint,
        )

        reg = StructureFingerprintRegistry()
        sig_a = structure_fingerprint({"name": "x"})
        sig_b = structure_fingerprint({"name": "x", "age": 30})

        reg.observe(sig_a)
        reg.observe(sig_a)
        reg.observe(sig_a)
        reg.observe(sig_b)

        top = reg.top_signatures(limit=10)
        assert top[0][0] == sig_a
        assert top[0][1] == 3
        assert top[1][0] == sig_b
        assert top[1][1] == 1

    def test_clear(self) -> None:
        from omnicrawl.core.structure_fingerprint import (
            StructureFingerprintRegistry,
            structure_fingerprint,
        )

        reg = StructureFingerprintRegistry()
        reg.observe(structure_fingerprint({"x": 1}))
        assert reg.unique_count() == 1
        reg.clear()
        assert reg.unique_count() == 0


class TestMetricsConsumer:
    """证明 structure_fingerprint 已接入 metrics.record_extracted 的非孤儿消费点。"""

    @staticmethod
    def _make_record(data: dict, *, source_url: str = "https://example.com/1", record_type: str = "html_item") -> object:
        class FakeRecord:
            def __init__(self) -> None:
                self.data = data
                self.source_url = source_url
                self.record_type = record_type

        return FakeRecord()

    def test_record_extracted_counts_template(self) -> None:
        from omnicrawl.services.metrics import RunMetrics

        metrics = RunMetrics()
        r1 = self._make_record({"name": "张三", "age": 30})
        r2 = self._make_record({"name": "李四", "age": 25})
        r3 = self._make_record({"name": "x", "price": 9.9})

        metrics.record_extracted(r1)
        metrics.record_extracted(r2)
        metrics.record_extracted(r3)

        snapshot = metrics.snapshot()
        template_counters = [
            e for e in snapshot["counters"] if e["name"] == "omnicrawl_structure_templates_total"
        ]
        # 两种结构模板（r1+r2 同结构，r3 不同）
        assert len(template_counters) == 2
        # snapshot 暴露 unique 模板数
        assert snapshot["structure_templates"]["unique"] == 2

    def test_record_extracted_detects_drift(self) -> None:
        from omnicrawl.services.metrics import RunMetrics

        metrics = RunMetrics()
        # 同 URL 先后出现两种结构 → 漂移计数
        metrics.record_extracted(
            self._make_record({"name": "x"}, source_url="https://example.com/p")
        )
        metrics.record_extracted(
            self._make_record({"name": "x", "age": 30}, source_url="https://example.com/p")
        )

        snapshot = metrics.snapshot()
        drift_counters = [
            e for e in snapshot["counters"] if e["name"] == "omnicrawl_structure_drift_total"
        ]
        assert len(drift_counters) == 1
        assert drift_counters[0]["value"] == 1

    def test_record_extracted_exception_does_not_break(self) -> None:
        from omnicrawl.services.metrics import RunMetrics

        metrics = RunMetrics()
        # 传入非法对象（无 data 属性）→ 不抛
        metrics.record_extracted(object())  # type: ignore[arg-type]
        # 后续正常 record 仍可工作
        metrics.record_extracted(self._make_record({"x": 1}))
        snapshot = metrics.snapshot()
        assert snapshot["structure_templates"]["unique"] == 1
