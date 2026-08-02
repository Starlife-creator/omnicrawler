"""日志解析器模块。

提供可配置的日志行解析，用于提取进度、统计信息等。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from re import Pattern


class LogParser:
    """日志行解析器。

    通过可配置的正则表达式和回调函数解析日志行，
    提取进度、错误、统计数据等信息。
    """

    # 默认进度正则：PROGRESS: 百分比 URL
    DEFAULT_PROGRESS_PATTERN: Pattern = re.compile(
        r"PROGRESS:\s*(\d+)%?\s*(https?://\S+)?"
    )

    # 统计信息正则
    STAT_PATTERNS: dict[str, Pattern] = {
        "pages": re.compile(r"(?:已处理|爬取|crawled?)\s*[:：]?\s*(\d+)\s*(?:页|pages?)"),
        "records": re.compile(r"(?:提取|extracted?)\s*[:：]?\s*(\d+)\s*(?:条|records?)"),
        "downloaded": re.compile(r"(?:下载|downloaded?)\s*[:：]?\s*(\d+)\s*(?:文件|files?)"),
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

        # 检测日志级别
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
