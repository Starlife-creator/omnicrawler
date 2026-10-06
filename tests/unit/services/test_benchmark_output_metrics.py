from dataclasses import replace

from omnicrawler.quality.output_metrics import record_quality_counts
from omnicrawler.services.benchmarking import BenchmarkResult, _dict_to_result, compare_benchmark


def test_unassessed_and_malformed_evidence_never_count_as_valid():
    clean = {"_quality": {"review_required": False, "validation_errors": [], "missing_required": []}}
    bad = {"_quality": {"review_required": False, "validation_errors": ["wrong number"], "missing_required": []}}
    result = record_quality_counts([clean, bad, {}, {"_quality": {"review_required": "false"}}])
    assert result == {"records": 4, "assessed": 2, "valid": 1, "review": 1, "unassessed": 2,
                      "invalid": 1, "duplicates": 0, "anomalies": 0}


def test_valid_output_comparison_does_not_follow_faster_pages_when_quality_falls():
    before = BenchmarkResult("fixture", 10, 10, 100, 200, 0, status="succeeded",
                             effective_config_sha256="config", input_sha256="input", environment=(("os", "same"),),
                             output_quality_scope="declared_contract_v1",
                             output_metrics=(("records", 10), ("valid", 10), ("unassessed", 0)))
    after = replace(before, duration_seconds=5, output_metrics=(("records", 10), ("valid", 2), ("unassessed", 0)))
    comparison = compare_benchmark(before, after)
    assert comparison["throughput_change"] == 1
    assert comparison["valid_output_throughput_change"] == -0.6
    assert comparison["valid_output_regression"] is True
    historical = _dict_to_result(before.to_mapping())
    assert historical.output_metrics == before.output_metrics
    assert historical.valid_records_per_second == 1
    legacy = replace(before, output_quality_scope="unmeasured", output_metrics=())
    assert legacy.valid_records_per_second is None
    assert compare_benchmark(legacy, after)["valid_output_comparable"] is False
    unknown = replace(before, output_metrics=(("records", 10), ("valid", 8), ("unassessed", 2)))
    assert unknown.valid_records_per_second is None
