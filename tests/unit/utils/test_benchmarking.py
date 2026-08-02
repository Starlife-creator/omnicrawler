"""Tests for the benchmarking module — profiles, runner, and history."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnicrawl.services.benchmarking import (
    PROFILES,
    BenchmarkHistory,
    BenchmarkProfile,
    BenchmarkResult,
    BenchmarkRunner,
    compare_benchmark,
    summarize_benchmarks,
)


class TestBenchmarkResult(unittest.TestCase):
    def test_pages_per_second(self):
        r = BenchmarkResult("standard", pages=100, duration_seconds=10.0, peak_memory_bytes=0, bytes_transferred=0, errors=0)
        self.assertAlmostEqual(r.pages_per_second, 10.0)

    def test_seconds_per_thousand_pages(self):
        r = BenchmarkResult("standard", pages=100, duration_seconds=10.0, peak_memory_bytes=0, bytes_transferred=0, errors=0)
        self.assertAlmostEqual(r.seconds_per_thousand_pages, 100.0)

    def test_zero_duration_safe(self):
        r = BenchmarkResult("low", pages=5, duration_seconds=0.0, peak_memory_bytes=0, bytes_transferred=0, errors=0)
        self.assertEqual(r.pages_per_second, 0.0)

    def test_to_mapping_includes_derived(self):
        r = BenchmarkResult("high", pages=50, duration_seconds=5.0, peak_memory_bytes=1024, bytes_transferred=2048, errors=1)
        m = r.to_mapping()
        self.assertIn("pages_per_second", m)
        self.assertIn("seconds_per_thousand_pages", m)
        self.assertEqual(m["profile"], "high")
        self.assertEqual(m["errors"], 1)


class TestSummarizeBenchmarks(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(summarize_benchmarks([]), {"runs": 0})

    def test_aggregation(self):
        results = [
            BenchmarkResult("low", 10, 5.0, 100, 1000, 0),
            BenchmarkResult("standard", 50, 10.0, 200, 5000, 2),
            BenchmarkResult("high", 200, 20.0, 300, 20000, 1),
        ]
        summary = summarize_benchmarks(results)
        self.assertEqual(summary["runs"], 3)
        self.assertEqual(summary["profiles"], ["high", "low", "standard"])
        self.assertEqual(summary["total_errors"], 3)
        self.assertEqual(summary["peak_memory_bytes"], 300)


class TestCompareBenchmark(unittest.TestCase):
    def test_no_regression(self):
        before = BenchmarkResult("standard", 100, 10.0, 100, 0, 0)
        after = BenchmarkResult("standard", 120, 10.0, 120, 0, 0)
        cmp = compare_benchmark(before, after)
        self.assertFalse(cmp["regression"])
        self.assertGreater(cmp["throughput_change"], 0)

    def test_regression_detected(self):
        before = BenchmarkResult("standard", 100, 10.0, 100, 0, 0)
        after = BenchmarkResult("standard", 80, 10.0, 120, 0, 0)
        cmp = compare_benchmark(before, after, regression_threshold=0.1)
        self.assertTrue(cmp["regression"])
        self.assertEqual(cmp["memory_change"], 20)

    def test_zero_baseline(self):
        before = BenchmarkResult("standard", 0, 10.0, 0, 0, 0)
        after = BenchmarkResult("standard", 10, 10.0, 0, 0, 0)
        cmp = compare_benchmark(before, after)
        self.assertFalse(cmp["regression"])


class TestProfiles(unittest.TestCase):
    def test_three_profiles_exist(self):
        self.assertIn("low", PROFILES)
        self.assertIn("standard", PROFILES)
        self.assertIn("high", PROFILES)

    def test_profile_attributes(self):
        p = PROFILES["high"]
        self.assertIsInstance(p.name, str)
        self.assertGreater(p.concurrency, 0)
        self.assertGreater(p.max_pages, 0)

    def test_high_has_more_concurrency_than_low(self):
        self.assertGreater(PROFILES["high"].concurrency, PROFILES["low"].concurrency)


class TestBenchmarkHistory(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bench_history.json"
            hist = BenchmarkHistory(path)
            result = BenchmarkResult("standard", 100, 10.0, 1024, 5000, 0)
            hist.add(result)
            self.assertTrue(path.is_file())

            hist2 = BenchmarkHistory(path)
            self.assertEqual(len(hist2.all_results()), 1)

    def test_latest_and_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bench_history.json"
            hist = BenchmarkHistory(path)
            r1 = BenchmarkResult("standard", 100, 10.0, 100, 0, 0)
            r2 = BenchmarkResult("standard", 120, 10.0, 110, 0, 0)
            hist.add(r1)
            hist.add(r2)

            latest = hist.latest("standard")
            self.assertIsNotNone(latest)
            self.assertEqual(latest.pages, 120)

            baseline = hist.baseline("standard")
            self.assertIsNotNone(baseline)
            self.assertEqual(baseline.pages, 100)

    def test_latest_nonexistent_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "bench.json")
            self.assertIsNone(hist.latest("nonexistent"))

    def test_check_regression_no_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "bench.json")
            result = BenchmarkResult("standard", 100, 10.0, 100, 0, 0)
            check = hist.check_regression(result)
            self.assertFalse(check["regression"])
            self.assertEqual(check["reason"], "no_baseline")

    def test_check_regression_with_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bench_history.json"
            hist = BenchmarkHistory(path)
            hist.add(BenchmarkResult("standard", 100, 10.0, 100, 0, 0))
            result = BenchmarkResult("standard", 80, 10.0, 120, 0, 0)
            check = hist.check_regression(result, threshold=0.1)
            self.assertTrue(check["regression"])

    def test_corrupt_history_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bench.json"
            path.write_text("not valid json", encoding="utf-8")
            hist = BenchmarkHistory(path)
            self.assertEqual(len(hist.all_results()), 0)


class TestBenchmarkRunner(unittest.TestCase):
    def test_custom_profiles(self):
        custom = {"custom": BenchmarkProfile("custom", 1, 1.0, 10, 5)}
        runner = BenchmarkRunner(profiles=custom)
        self.assertIn("custom", runner._profiles)

    def test_unknown_profile_raises(self):
        runner = BenchmarkRunner()
        with self.assertRaises(KeyError):
            runner.run("nonexistent", config_path="dummy.yaml")


if __name__ == "__main__":
    unittest.main()
