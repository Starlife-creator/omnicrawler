"""Probe the installed renderer against an owned local fixture, without account state."""
from __future__ import annotations

import copy
import importlib.metadata
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.models import CrawlRequest


def fallback_policy(config: AppConfig) -> dict[str, Any]:
    reasons = []
    if config.section("session").get("persist_cookies"):
        reasons.append("saved_session")
    if config.section("browser").get("persist_profile"):
        reasons.append("persistent_profile")
    if config.section("source").get("auth_check"):
        reasons.append("authentication_check")
    enabled = config.section("browser").get("selenium_fallback_engine") == "playwright"
    return {"allowed": enabled and not reasons, "blocking_conditions": reasons,
            "detail": "有会话或认证要求时不自动换引擎；请使用原引擎更新登录后恢复。" if reasons else "只有显式配置的公开任务允许回退。"}


def probe(config: AppConfig) -> dict[str, Any]:
    from ..fetching.browser_fetcher import BrowserFetcher

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"<html><body><div id='probe'>omnicrawler-owned-renderer-probe</div></body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    versions = {}
    for name in ("playwright", "selenium"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    report: dict[str, Any] = {"engine": config.section("browser").get("engine", "playwright"), "versions": versions,
                             "fallback": fallback_policy(config), "scope": "owned_local_fixture_no_account_or_task_actions"}
    if report["engine"] == "selenium":
        driver = os.environ.get("OMNICRAWL_SELENIUM_DRIVER", "").strip() or shutil.which("chromedriver")
        if not driver or not Path(driver).is_file():
            return {**report, "status": "unavailable", "error_type": "LocalDriverMissing", "duration_seconds": 0,
                    "detail": "未找到本机 ChromeDriver；请选择已安装驱动。兼容性检查不会自动下载组件。"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="omnicrawler-renderer-probe-") as directory:
            root = Path(directory)
            raw = copy.deepcopy(config.raw)
            raw["source"] = {"kind": "browser", "seeds": [f"http://127.0.0.1:{server.server_port}/"]}
            raw["session"] = {"persist_cookies": False}
            raw["browser"].update(actions=[], launch_args=[], headless=True, persist_profile=False, selenium_fallback_engine="")
            raw["browser"].pop("api_capture", None)
            raw["http"].update(proxy="", headers={}, delay_seconds=0, timeout_seconds=5, selenium_watchdog_seconds=10,
                               allow_private_network=True, respect_robots=False)
            raw["egress"].update(enabled=True, allowed_domains=["127.0.0.1"], credential_domains=[], audit=True)
            raw["crawl"]["allow_domains"] = ["127.0.0.1"]
            raw["project"]["workspace"] = str(root)
            raw["project"]["root"] = str(root)
            raw["ai"] = {"mode": "disabled", "providers": {}}
            raw["storage"] = {}
            isolated = AppConfig(root / "probe.yaml", root, raw, root)
            fetcher = BrowserFetcher(isolated)
            try:
                result = fetcher.fetch(CrawlRequest(raw["source"]["seeds"][0], render=True))
                if b"omnicrawler-owned-renderer-probe" not in result.body:
                    raise ValueError("Renderer probe content did not match")
                report.update(status="passed", actual_engine=report["engine"], detail="本机引擎完成了受控页面渲染与出口拦截；真实站点仍需试跑。")
            finally:
                fetcher.close()
    except Exception as exc:
        report.update(status="unavailable", error_type=type(exc).__name__, detail="引擎探测未通过。检查浏览器和驱动组合；有会话的任务请先修复原引擎，再重新登录。")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    return report
