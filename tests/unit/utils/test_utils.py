import gzip
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from omnicrawler.core.utils import (
    atomic_write,
    canonicalize_url,
    deep_merge,
    excel_safe,
    expand_env,
    json_text,
    redact_headers,
    safe_filename,
    sha256_bytes,
    utcnow,
)
from omnicrawler.fetching.http_client import HTTPFetcher
from omnicrawler.security.policy import HostRateLimiter, is_private_target


class UtilsTest(unittest.TestCase):
    def test_canonicalize_and_scheme_filter(self):
        self.assertEqual(
            canonicalize_url("https://Example.com/a/", "../b?q=1#fragment"),
            "https://example.com/b?q=1",
        )
        self.assertIsNone(canonicalize_url("https://example.com", "javascript:alert(1)"))

    def test_filename_and_excel_safety(self):
        name = safe_filename("https://example.com/download?id=1", "application/pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertNotIn("?", name)
        self.assertEqual(excel_safe("=1+1"), "'=1+1")
        self.assertEqual(excel_safe("-12.5"), "-12.5")

    def test_excel_safe_defeats_whitespace_prefix_injection(self):
        """S1.3.1：`\t=cmd`、`\r@x` 等前导空白注入必须被护栏捕获。"""
        self.assertEqual(excel_safe("\t=cmd|' /C calc'!A0"), "'\t=cmd|' /C calc'!A0")
        self.assertEqual(excel_safe("\r@x"), "'\r@x")
        self.assertEqual(excel_safe(" +1+1"), "' +1+1")
        self.assertEqual(excel_safe("@SUM(A1:A2)"), "'@SUM(A1:A2)")
        self.assertEqual(excel_safe("plain text"), "plain text")
        self.assertEqual(excel_safe("42"), "42")
        self.assertEqual(excel_safe(" 12"), " 12")

    def test_private_targets(self):
        self.assertTrue(is_private_target("http://127.0.0.1/test"))
        self.assertTrue(is_private_target("http://localhost/test"))
        self.assertTrue(is_private_target("http://[::1]/test"))
        self.assertFalse(is_private_target("https://example.com/"))

    def test_rate_limiter_does_not_serialize_different_hosts(self):
        # 用可控假时钟替代真实墙钟断言，消除 CI 负载下的 flaky（原 assertLess(<0.26) 依赖真实墙钟）
        fake_clock = {"t": 1000.0}
        real_monotonic = time.monotonic
        real_sleep = time.sleep

        def fake_monotonic():
            return fake_clock["t"]

        def fake_sleep(duration):
            # 模拟阻塞：直接推进假时钟，不真正等待
            fake_clock["t"] += duration

        time.monotonic = fake_monotonic  # type: ignore[assignment]
        time.sleep = fake_sleep  # type: ignore[assignment]
        try:
            limiter = HostRateLimiter(0.15)
            limiter.wait("https://a.example/first")
            limiter.wait("https://b.example/first")
            started = fake_monotonic()
            threads = [
                threading.Thread(target=limiter.wait, args=("https://a.example/second",)),
                threading.Thread(target=limiter.wait, args=("https://b.example/second",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            elapsed = fake_monotonic() - started
            # 不同 host 各有独立槽位：并行两段等待的模拟耗时 < 被串行化的 2×delay(=0.30)
            self.assertLess(elapsed, 2 * limiter.delay)
        finally:
            time.monotonic = real_monotonic
            time.sleep = real_sleep

    def test_compressed_response_limit_is_enforced_during_decode(self):
        body = gzip.compress(b"x" * 10_000)
        with self.assertRaisesRegex(ValueError, "超过大小限制"):
            HTTPFetcher._decode_content(body, "gzip", 100)
        self.assertEqual(HTTPFetcher._decode_content(gzip.compress(b"ok"), "gzip", 100), b"ok")


class ExpandEnvTest(unittest.TestCase):
    """Test expand_env — environment variable substitution."""

    def test_plain_string_no_variables(self):
        self.assertEqual(expand_env("hello"), "hello")

    def test_env_variable_substitution(self):
        os.environ["TEST_ENV_VAR"] = "world"
        try:
            self.assertEqual(expand_env("hello_${TEST_ENV_VAR}"), "hello_world")
        finally:
            del os.environ["TEST_ENV_VAR"]

    def test_env_variable_with_default(self):
        self.assertEqual(expand_env("${MISSING_VAR:-default_value}"), "default_value")

    def test_env_variable_missing_no_default(self):
        self.assertEqual(expand_env("${MISSING_VAR}"), "")

    def test_expand_in_list(self):
        os.environ["TEST_LIST"] = "x"
        try:
            self.assertEqual(expand_env(["${TEST_LIST}", "y"]), ["x", "y"])
        finally:
            del os.environ["TEST_LIST"]

    def test_expand_in_dict(self):
        os.environ["TEST_DICT"] = "z"
        try:
            self.assertEqual(expand_env({"k": "${TEST_DICT}"}), {"k": "z"})
        finally:
            del os.environ["TEST_DICT"]

    def test_expand_non_string_values(self):
        self.assertEqual(expand_env(42), 42)
        self.assertEqual(expand_env(None), None)


class DeepMergeTest(unittest.TestCase):
    """Test deep_merge — recursive dict merging."""

    def test_shallow_merge(self):
        self.assertEqual(deep_merge({"a": 1}, {"b": 2}), {"a": 1, "b": 2})

    def test_override_value(self):
        self.assertEqual(deep_merge({"a": 1}, {"a": "updated"}), {"a": "updated"})

    def test_nested_merge(self):
        base = {"outer": {"inner_a": 1, "inner_b": 2}}
        override = {"outer": {"inner_b": 99, "inner_c": 3}}
        result = deep_merge(base, override)
        self.assertEqual(result, {"outer": {"inner_a": 1, "inner_b": 99, "inner_c": 3}})

    def test_override_dict_with_scalar(self):
        self.assertEqual(
            deep_merge({"outer": {"a": 1}}, {"outer": "replaced"}),
            {"outer": "replaced"},
        )

    def test_empty_override_no_change(self):
        self.assertEqual(deep_merge({"a": 1}, {}), {"a": 1})

    def test_result_is_deep_copy_of_base(self):
        base = {"outer": {"inner": [1, 2]}}
        result = deep_merge(base, {"extra": 1})
        result["outer"]["inner"].append(3)
        result["outer"]["inner"] = []
        self.assertEqual(base, {"outer": {"inner": [1, 2]}})

    def test_nested_result_does_not_share_list_with_base(self):
        base = {"items": ["a"]}
        result = deep_merge(base, {"count": 1})
        result["items"].append("b")
        self.assertEqual(base["items"], ["a"])


class CanonicalizeUrlEdgeTest(unittest.TestCase):
    """Test canonicalize_url edge cases."""

    def test_invalid_url_returns_none(self):
        self.assertIsNone(canonicalize_url("https://example.com", "invalid://url with spaces"))

    def test_sort_query_true(self):
        result = canonicalize_url("https://example.com/a", "b?z=1&a=2", sort_query=True)
        self.assertIn("a=2", result)
        self.assertIn("z=1", result)
        # sorted order: a=2 comes before z=1
        self.assertTrue(result.index("a=2") < result.index("z=1"))

    def test_missing_hostname_returns_none(self):
        # urlsplit of scheme-only URL
        self.assertIsNone(canonicalize_url("http:///path", "/x"))

    def test_fragment_removed(self):
        result = canonicalize_url("https://example.com/page", "#section")
        self.assertNotIn("#", result)

    def test_https_default_port_removed(self):
        result = canonicalize_url("https://example.com:443/path", "/x")
        self.assertNotIn(":443", result)

    def test_ipv6_bracket_preserved(self):
        result = canonicalize_url("http://[::1]/a", "/b")
        self.assertIn("[::1]", result)


class UtilsMiscTest(unittest.TestCase):
    """Test remaining utility functions."""

    def test_sha256_bytes(self):
        self.assertEqual(
            sha256_bytes(b"hello"),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        )

    def test_utcnow_format(self):
        now = utcnow()
        self.assertRegex(now, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_json_text_sorts_keys(self):
        result = json_text({"b": 2, "a": 1})
        self.assertIn('"a"', result)
        self.assertIn('"b"', result)
        self.assertTrue(result.index('"a"') < result.index('"b"'))

    def test_json_text_handles_non_serializable(self):
        from datetime import date
        result = json_text({"d": date(2024, 1, 1)})
        self.assertIn("2024-01-01", result)

    def test_redact_headers_removes_sensitive(self):
        headers = {
            "authorization": "Bearer secret123",
            "content-type": "text/html",
            "cookie": "session=abc",
            "set-cookie": "id=123",
            "x-api-key": "key-secret",
            "accept": "text/html",
        }
        redacted = redact_headers(headers)
        self.assertEqual(redacted, {"content-type": "text/html", "accept": "text/html"})

    def test_redact_headers_keeps_non_sensitive(self):
        headers = {"content-type": "text/html", "user-agent": "omnicrawler"}
        self.assertEqual(redact_headers(headers), headers)

    def test_redact_headers_filters_suffix_api_key(self):
        headers = {"my-api-key": "secret"}
        self.assertEqual(redact_headers(headers), {})


class AtomicWriteTest(unittest.TestCase):
    """Test atomic_write — atomic file write with cleanup."""

    def test_atomic_write_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subdir" / "output.txt"
            atomic_write(path, b"hello world")
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), b"hello world")

    def test_atomic_write_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deep" / "nested" / "file.bin"
            atomic_write(path, b"data")
            self.assertTrue(path.is_file())

    def test_atomic_write_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.txt"
            atomic_write(path, b"first")
            atomic_write(path, b"second")
            self.assertEqual(path.read_bytes(), b"second")


if __name__ == "__main__":
    unittest.main()
