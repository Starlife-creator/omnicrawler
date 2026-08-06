"""日志解析器模块。

提供可配置的日志行解析，用于提取进度、统计信息等。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from re import Pattern

from ..i18n import _


class LogParser:
    """日志行解析器。

    通过可配置的正则表达式和回调函数解析日志行，
    提取进度、错误、统计数据等信息。
    """

    # 默认进度正则：PROGRESS: 百分比 URL
    DEFAULT_PROGRESS_PATTERN: Pattern = re.compile(
        r"PROGRESS:\s*(\d+)%?\s*(https?://\S+)?"
    )

    # 统计信息正则（S3.3.1：匹配真实 CLI 输出——"提取记录: 45"/"采集页面: 3"/"下载附件: 2"）
    STAT_PATTERNS: dict[str, Pattern] = {
        "pages": re.compile(
            _(r"(?:已处理|爬取|采集|crawled?)\s*(?:页面|页|pages?)?\s*[:：]?\s*(\d+)")
        ),
        "records": re.compile(
            _(r"(?:提取|extracted?)\s*(?:记录|条|records?)?\s*[:：]?\s*(\d+)")
        ),
        "downloaded": re.compile(
            _(r"(?:下载|downloaded?)\s*(?:附件|文件|files?)?\s*[:：]?\s*(\d+)")
        ),
    }

    def __init__(
        self,
        progress_pattern: Pattern | None = None,
        on_progress: Callable[[int, str], None] | None = None,
    ) -> None:
        """初始化日志解析器。

        Args:
            progress_pattern: 自定义进度解析正则。
            on_progress: 进度回调函数 (percent, url)。
        """
        self._progress_pattern = progress_pattern or self.DEFAULT_PROGRESS_PATTERN
        self._on_progress = on_progress
        self._stats: dict[str, int] = {}

    def parse_line(self, line: str) -> dict:
        """解析单行日志。

        Args:
            line: 日志行文本。

        Returns:
            包含解析结果的字典：
            - 'level': 日志级别 (info/warn/error)
            - 'progress': 进度信息 (percent, url) 或 None
            - 'stats': 匹配到的统计信息字典
        """
        result: dict = {
            "level": "info",
            "progress": None,
            "stats": {},
        }

        # 检测日志级别（S3.3.1：显式级别前缀优先——真实 CLI 的
        # "WARNING: ..." / "ERROR: ..." 前缀是权威级别，避免
        # "PermissionError" 等类型名中的 error 子串误判级别）
        prefix_match = re.match(r"^\[?(WARNING|ERROR|INFO|WARN)\]?\s*:", line, re.I)
        if prefix_match:
            prefix = prefix_match.group(1).casefold()
            result["level"] = "warn" if prefix in {"warning", "warn"} else "error" if prefix == "error" else "info"
        else:
            lower = line.lower()
            if any(kw in lower for kw in ("error", "exception", "traceback", "failed")):
                result["level"] = "error"
            elif any(kw in lower for kw in ("warn", "warning")):
                result["level"] = "warn"

        # 解析进度
        progress_match = self._progress_pattern.search(line)
        if progress_match:
            try:
                percent = int(progress_match.group(1))
                url = progress_match.group(2) or ""
                result["progress"] = {"percent": percent, "url": url.strip()}
                if self._on_progress:
                    self._on_progress(percent, url.strip())
            except (ValueError, IndexError):
                pass

        # 解析统计信息
        for key, pattern in self.STAT_PATTERNS.items():
            stat_match = pattern.search(line)
            if stat_match:
                try:
                    count = int(stat_match.group(1))
                    self._stats[key] = count
                    result["stats"][key] = count
                except (ValueError, IndexError):
                    pass

        return result

    def get_stats(self) -> dict[str, int]:
        """获取累积统计信息。"""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """重置统计信息。"""
        self._stats.clear()
