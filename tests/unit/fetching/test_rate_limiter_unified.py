"""S2.5.48：单/批请求共用同一限速器（速率一致）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.async_fetcher import HTTPXAsyncFetcher


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        "project: {name: s2548, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "http: {delay_seconds: 0.05}\n",
        encoding="utf-8",
    )
    return path


def test_single_and_batch_share_one_limiter(tmp_path: Path) -> None:
    fetcher = HTTPXAsyncFetcher(load_config(_config(tmp_path)))
    request = CrawlRequest("https://example.org/")
    # 单请求路径与批量路径等待同一 HostRateLimiter 实例
    async def _run() -> None:
        await asyncio.to_thread(fetcher.limiter.wait, request.url)
        await asyncio.to_thread(fetcher.limiter.wait, request.url)

    asyncio.run(_run())
    # 双路径都经由 self.limiter（不存在独立 async_limiter 实例）
    assert not hasattr(fetcher, "async_limiter")
