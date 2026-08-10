import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    from datetime import UTC, datetime, timedelta  # Python 3.11+
except ImportError:
    from datetime import datetime, timedelta, timezone
    UTC = timezone.utc

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.errors import PolicyBlockedError
from omnicrawl.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawl.extraction.extractors import _apply_rule
from omnicrawl.extraction.html_tools import MiniNode, _mini_select, parse_html
from omnicrawl.fetching.http_client import PinnedHTTPConnection, PinnedHTTPSConnection, SafeRedirectHandler
from omnicrawl.fetching.retry import retry_after_seconds
from omnicrawl.fetching.routing import needs_browser
from omnicrawl.plugins.plugins import Registry, load_local_plugins
from omnicrawl.quality.diagnostics import DiagnosticRecorder
from omnicrawl.quality.quality import assess_record
from omnicrawl.security.policy import AsyncHostRateLimiter, NetworkTargetPolicy, RobotsPolicy


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _maximum):
        time.sleep(0.1)
        return b"User-agent: *\nAllow: /\n"


class _Opener:
    def __init__(self):
        self.urls = []

    def open(self, request, timeout):
        self.urls.append((request.full_url, timeout))
        return _Response()


class ProductionFoundationTest(unittest.TestCase):
    def test_mini_selector_handles_many_siblings_without_recursive_equality(self):
        root = MiniNode("document")
        for index in range(200):
            root.children.append(MiniNode("article", {"data-index": str(index)}, root))
        self.assertEqual(len(_mini_select(root, "article")), 200)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def config(self, extra=None):
        value = {
            "project": {"name": "test", "workspace": "work/test"},
            "source": {"kind": "static_html", "seeds": ["https://example.com/"]},
            "http": {"resolve_dns": False},
        }
        if extra:
            for key, item in extra.items():
                value.setdefault(key, {}).update(item)
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(value), encoding="utf-8")
        return load_config(path)

    def test_dns_resolution_blocks_private_result(self):
        policy = NetworkTargetPolicy(self.config({"http": {"resolve_dns": True}}))
        answer = [(2, 1, 6, "", ("169.254.169.254", 80))]
        with patch("socket.getaddrinfo", return_value=answer):
            allowed, reason = policy.allowed("http://metadata.example/latest")
        self.assertFalse(allowed)
        self.assertIn("内网或保留地址", reason)

    def test_dns_resolution_allows_public_result(self):
        policy = NetworkTargetPolicy(self.config({"http": {"resolve_dns": True}}))
        answer = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("socket.getaddrinfo", return_value=answer):
            self.assertEqual(policy.allowed("https://example.com/"), (True, ""))

    def test_dns_resolution_blocks_mixed_public_and_private_answers(self):
        policy = NetworkTargetPolicy(self.config({"http": {"resolve_dns": True}}))
        answer = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.8", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=answer):
            allowed, reason = policy.allowed("https://mixed.example/")
        self.assertFalse(allowed)
        self.assertIn("10.0.0.8", reason)

    def test_connection_rechecks_and_blocks_dns_rebinding(self):
        config = self.config({"http": {"resolve_dns": True, "dns_cache_ttl_seconds": 0}})
        policy = NetworkTargetPolicy(config)
        public = [(2, 1, 6, "", ("93.184.216.34", 80))]
        rebound = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with patch("socket.getaddrinfo", side_effect=[public, rebound]):
            self.assertEqual(policy.allowed("http://rebind.example/"), (True, ""))
            connection = PinnedHTTPConnection("rebind.example", target_policy=policy)
            with self.assertRaises(PolicyBlockedError):
                connection.connect()

    def test_connection_uses_approved_address_literal(self):
        policy = NetworkTargetPolicy(self.config({"http": {"resolve_dns": True}}))
        answer = [(2, 1, 6, "", ("93.184.216.34", 80))]
        sock = MagicMock()
        with (
            patch("socket.getaddrinfo", return_value=answer),
            patch("socket.create_connection", return_value=sock) as create_connection,
        ):
            connection = PinnedHTTPConnection("example.com", target_policy=policy)
            connection.connect()
        self.assertEqual(create_connection.call_args.args[0], ("93.184.216.34", 80))

    def test_https_connection_preserves_original_sni(self):
        policy = NetworkTargetPolicy(self.config({"http": {"resolve_dns": True}}))
        answer = [(2, 1, 6, "", ("93.184.216.34", 443))]
        raw_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = MagicMock()
        with (
            patch("socket.getaddrinfo", return_value=answer),
            patch("socket.create_connection", return_value=raw_socket),
        ):
            connection = PinnedHTTPSConnection(
                "example.com", target_policy=policy, context=context
            )
            connection.connect()
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="example.com")

    def test_async_limiter_keeps_hosts_independent(self):
        async def run():
            limiter = AsyncHostRateLimiter(0.1)
            await asyncio.gather(limiter.wait("https://a.example"), limiter.wait("https://b.example"))
            started = time.monotonic()
            await asyncio.gather(limiter.wait("https://a.example/2"), limiter.wait("https://b.example/2"))
            return time.monotonic() - started
        self.assertLess(asyncio.run(run()), 0.17)

    def test_robots_fetches_different_origins_concurrently(self):
        opener = _Opener()
        policy = RobotsPolicy(
            self.config({"http": {"respect_robots": True}}), opener=opener
        )
        values = []
        started = time.monotonic()
        threads = [
            threading.Thread(target=lambda url=url: values.append(policy.allowed(url)))
            for url in ("https://a.example/page", "https://b.example/page")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(all(values))
        self.assertLess(time.monotonic() - started, 0.18)
        self.assertEqual(
            {url for url, _timeout in opener.urls},
            {"https://a.example/robots.txt", "https://b.example/robots.txt"},
        )

    def test_retry_after_accepts_http_date(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        target = (now + timedelta(seconds=15)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(retry_after_seconds({"Retry-After": target}, now=now), 15)

    def test_multi_candidate_selector_falls_back(self):
        document = parse_html("<html><h1 class='title'>Wanted</h1></html>")
        value, evidence = _apply_rule(document, {"selectors": [".missing", "h1.title"]})
        self.assertEqual(value, "Wanted")
        self.assertEqual(evidence["candidate"], 1)

    def test_quality_marks_missing_required_field(self):
        record = ExtractedRecord("https://example.com", "item", {"title": "ok"})
        result = assess_record(record, {"title": {"required": True}, "date": {"required": True}})
        self.assertTrue(result["review_required"])
        self.assertEqual(result["missing_required"], ["date"])

    def test_js_shell_escalates_to_browser(self):
        body = b"<html><div id='root'></div><script></script><script></script><script></script></html>"
        result = FetchResult(CrawlRequest("https://example.com"), "https://example.com", 200, {"content-type": "text/html"}, body, 0.1)
        self.assertTrue(needs_browser(result)[0])

    def test_redirect_handler_validates_every_hop(self):
        class Guard:
            def __init__(self):
                self.urls = []

            def require(self, url):
                self.urls.append(url)

        guard = Guard()
        handler = SafeRedirectHandler(guard, 5)
        import urllib.request
        request = urllib.request.Request("https://example.com/start")
        redirected = handler.redirect_request(
            request, None, 302, "Found", {}, "https://cdn.example.com/next"
        )
        self.assertEqual(guard.urls, ["https://cdn.example.com/next"])
        self.assertEqual(redirected.full_url, "https://cdn.example.com/next")

    def test_redirect_handler_resolves_relative_url_before_validation(self):
        class Guard:
            def __init__(self):
                self.urls = []

            def require(self, url):
                self.urls.append(url)

        guard = Guard()
        handler = SafeRedirectHandler(guard, 5)
        import urllib.request
        request = urllib.request.Request("https://example.com/folder/start")
        redirected = handler.redirect_request(request, None, 302, "Found", {}, "../next")
        self.assertEqual(guard.urls, ["https://example.com/next"])
        self.assertEqual(redirected.full_url, "https://example.com/next")

    def test_diagnostic_package_redacts_sensitive_headers(self):
        recorder = DiagnosticRecorder(self.root, {"project": "test"})
        request = CrawlRequest(
            "https://example.com/private",
            headers={"Authorization": "Bearer secret-value", "Accept": "text/html"},
        )
        path = recorder.failure("run1", "fetch", RuntimeError("boom"), request=request)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("secret-value", text)
        self.assertIn('"Accept": "text/html"', text)

    def test_plugin_contract_reports_metadata(self):
        plugin = self.root / "plugin.py"
        plugin.write_text(
            "PLUGIN_METADATA={'name':'demo','version':'1.2.3','api_version':1,'min_core_version':'0.0.1'}\n"
            "def register(registry):\n"
            "    registry.register_source('demo_source', object)\n",
            encoding="utf-8",
        )
        registry = Registry()
        load_local_plugins(registry, [str(plugin)], self.root)
        self.assertEqual(registry.describe()["plugins"], ["demo@1.2.3"])
        self.assertEqual(registry.describe()["plugin_details"][0]["execution_mode"], "in_process_trusted")

    def test_plugin_dynamic_metadata_cannot_bypass_approval_gate(self):
        """S1.3.7：动态计算的 PLUGIN_METADATA 无法绕过权限审批门。"""
        plugin = self.root / "dynamic.py"
        plugin.write_text(
            "PLUGIN_METADATA = dict(name='sneaky', version='0.1', api_version=1,"
            " min_core_version='0.0.1', permissions=['network'], domains=['example.com'])\n"
            "def register(registry):\n"
            "    registry.register_source('sneaky_source', object)\n",
            encoding="utf-8",
        )
        registry = Registry()
        with self.assertRaises(PermissionError) as context:
            load_local_plugins(registry, [str(plugin)], self.root)
        self.assertIn("静态字面量", str(context.exception))
        self.assertEqual(registry.describe()["plugins"], [])

    def test_plugin_literal_network_permission_still_requires_approval(self):
        """S1.3.7：字面量 network 权限同样必须显式 approve 才能加载。"""
        plugin = self.root / "literal.py"
        plugin.write_text(
            "PLUGIN_METADATA={'name':'net','version':'0.1','api_version':1,"
            "'min_core_version':'0.0.1','permissions':['network'],'domains':['api.example.com']}\n"
            "def register(registry):\n"
            "    registry.register_source('net_source', object)\n",
            encoding="utf-8",
        )
        registry = Registry()
        with self.assertRaises(PermissionError) as context:
            load_local_plugins(registry, [str(plugin)], self.root)
        self.assertIn("Plugin permissions were not approved", str(context.exception))


if __name__ == "__main__":
    unittest.main()
