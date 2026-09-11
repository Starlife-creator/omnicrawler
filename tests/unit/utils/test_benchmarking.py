"""Tests for the benchmarking module — profiles, runner, and history."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnicrawler.services.benchmarking import (
    PROFILES,
    BenchmarkHistory,
    BenchmarkProfile,
    BenchmarkResult,
    BenchmarkRunner,
    _stored_bytes,
    _summary_stats,
    apply_profile,
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




class TestApplyProfile(unittest.TestCase):
    """profile 必须真正改写配置——这是「公平对比」的前提。

    2026-09-11 之前 profile 只作为标签记录，档位参数从未施加到运行配置。
    """

    def test_applies_crawl_and_http_settings(self):
        raw = {"crawl": {"concurrency": 4, "max_pages": 500}, "http": {"delay_seconds": 1.0}}
        effective = apply_profile(raw, PROFILES["low"])
        self.assertEqual(effective["crawl"]["concurrency"], 1)
        self.assertEqual(effective["crawl"]["max_pages"], 10)
        self.assertEqual(effective["http"]["delay_seconds"], 2.0)
        self.assertEqual(effective["http"]["timeout_seconds"], 30)

    def test_does_not_mutate_source(self):
        raw = {"crawl": {"max_pages": 500}, "http": {"delay_seconds": 1.0}}
        apply_profile(raw, PROFILES["high"])
        self.assertEqual(raw["crawl"]["max_pages"], 500)
        self.assertEqual(raw["http"]["delay_seconds"], 1.0)
        self.assertNotIn("timeout_seconds", raw["http"])

    def test_missing_sections_are_created(self):
        effective = apply_profile({}, PROFILES["standard"])
        self.assertEqual(effective["crawl"]["concurrency"], 3)
        self.assertEqual(effective["http"]["timeout_seconds"], 25)

    def test_non_mapping_section_raises(self):
        with self.assertRaises(ValueError):
            apply_profile({"crawl": ["not", "a", "mapping"]}, PROFILES["low"])

    def test_profiles_differ_in_workload(self):
        # 三个档位若只有名字不同，就无所谓「公平对比」——锁住它们确实不同。
        self.assertEqual(len({p.max_pages for p in PROFILES.values()}), 3)
        self.assertEqual(len({p.concurrency for p in PROFILES.values()}), 3)


class TestUsable(unittest.TestCase):
    """ok 陈述运行结局；usable 才决定能否当基线。两者必须分开。"""

    def test_success_with_pages_is_usable(self):
        self.assertTrue(BenchmarkResult("low", 10, 5.0, 0, 0, 0, status="succeeded").usable)

    def test_failure_is_not_usable(self):
        self.assertFalse(BenchmarkResult("low", 10, 5.0, 0, 0, 0, ok=False, status="failed").usable)

    def test_zero_pages_is_not_usable(self):
        # 实测：种子全部连接失败时流水线仍报 succeeded，pages=0。
        # 这种空白运行若被当作基线，该档位的退化检测就形同虚设。
        result = BenchmarkResult("low", 0, 5.0, 0, 0, 0, status="succeeded")
        self.assertTrue(result.ok)
        self.assertFalse(result.usable)


class TestResultMetadata(unittest.TestCase):
    """结果必须自带复现信息，否则两次运行没法证明可比。"""

    def test_to_mapping_flattens_pairs(self):
        result = BenchmarkResult("low", 10, 5.0, 0, 0, 0, status="succeeded",
                                 config_sha256="abc", effective_config_sha256="def",
                                 profile_settings=(("max_pages", "10"),),
                                 environment=(("package_version", "9.9"),))
        payload = result.to_mapping()
        self.assertEqual(payload["profile_settings"], {"max_pages": "10"})
        self.assertEqual(payload["environment"], {"package_version": "9.9"})
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["config_sha256"], "abc")

    def test_history_round_trip_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "h.json"
            hist = BenchmarkHistory(path)
            hist.add(BenchmarkResult("low", 10, 5.0, 0, 0, 0, status="succeeded",
                                     config_sha256="abc", effective_config_sha256="def",
                                     profile_settings=(("max_pages", "10"),),
                                     environment=(("python", "3.13"),)))
            restored = BenchmarkHistory(path).latest("low")
            self.assertIsNotNone(restored)
            self.assertEqual(dict(restored.profile_settings), {"max_pages": "10"})
            self.assertEqual(dict(restored.environment), {"python": "3.13"})
            self.assertEqual(restored.config_sha256, "abc")
            self.assertEqual(restored.effective_config_sha256, "def")
            self.assertEqual(restored.status, "succeeded")
            self.assertTrue(restored.usable)

    def test_legacy_entry_without_metadata_still_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "h.json"
            path.write_text('[{"profile": "low", "pages": 5, "duration_seconds": 2.0, '
                            '"peak_memory_bytes": 10, "bytes_transferred": 0, '
                            '"errors": 0}]', encoding="utf-8")
            restored = BenchmarkHistory(path).baseline("low")
            self.assertIsNotNone(restored)
            # 旧条目无 ok/status：ok 取 True（无法回溯判定），元数据为空。
            self.assertTrue(restored.ok)
            self.assertEqual(restored.status, "")
            self.assertEqual(restored.profile_settings, ())


class TestBaselineSkipsUnusable(unittest.TestCase):
    """基线必须只取可用运行——否则零吞吐基线会让退化永远检不出来。"""

    def test_zero_page_run_does_not_become_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "h.json")
            hist.add(BenchmarkResult("low", 0, 9.0, 0, 0, 0, status="succeeded"))
            self.assertIsNone(hist.baseline("low"))

    def test_failed_run_does_not_become_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "h.json")
            hist.add(BenchmarkResult("low", 0, 9.0, 0, 0, 1, ok=False, status="failed"))
            self.assertIsNone(hist.baseline("low"))

    def test_first_usable_run_is_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "h.json")
            hist.add(BenchmarkResult("low", 0, 9.0, 0, 0, 0, status="succeeded"))
            hist.add(BenchmarkResult("low", 20, 10.0, 0, 0, 0, status="succeeded"))
            hist.add(BenchmarkResult("low", 30, 10.0, 0, 0, 0, status="succeeded"))
            baseline = hist.baseline("low")
            self.assertIsNotNone(baseline)
            self.assertEqual(baseline.pages, 20)

    def test_regression_detected_against_usable_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            hist = BenchmarkHistory(Path(temp) / "h.json")
            hist.add(BenchmarkResult("low", 100, 10.0, 0, 0, 0, status="succeeded"))
            slower = BenchmarkResult("low", 50, 10.0, 0, 0, 0, status="succeeded")
            self.assertTrue(hist.check_regression(slower)['regression'])


class TestSummaryStats(unittest.TestCase):
    """流水线摘要是展平结构——读错键曾让全部指标恒为 0。"""

    def test_flat_summary_used_directly(self):
        flat = {"run_id": "x", "status": "succeeded", "responses": 12, "errors": 0}
        self.assertEqual(_summary_stats(flat)["responses"], 12)

    def test_nested_stats_dict_takes_precedence(self):
        nested = {"status": "succeeded", "stats": {"responses": 7}}
        self.assertEqual(_summary_stats(nested)["responses"], 7)

    def test_non_mapping_stats_falls_back_to_flat(self):
        self.assertEqual(_summary_stats({"responses": 3, "stats": None})["responses"], 3)


class TestStoredBytes(unittest.TestCase):
    """落库字节数从状态库聚合；缺失时安静回落到 0，不抛异常。"""

    def test_missing_inputs_return_zero(self):
        self.assertEqual(_stored_bytes(None, "run"), 0)
        self.assertEqual(_stored_bytes(Path("whatever"), ""), 0)

    def test_absent_database_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(_stored_bytes(Path(temp), "run"), 0)

    def test_aggregates_stored_response_sizes(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "state.sqlite3"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE responses (run_id TEXT, size_bytes INTEGER)")
            conn.executemany("INSERT INTO responses VALUES (?, ?)",
                             [("r1", 100), ("r1", 250), ("r2", 999)])
            conn.commit()
            conn.close()
            self.assertEqual(_stored_bytes(Path(temp), "r1"), 350)
            self.assertEqual(_stored_bytes(Path(temp), "missing"), 0)


if __name__ == "__main__":
    unittest.main()
