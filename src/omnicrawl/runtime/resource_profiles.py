from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from ..core.config import AppConfig


@dataclass(frozen=True, slots=True)
class ResourceProfile:
    name: str
    concurrency_cap: int
    browser_pool_cap: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def profile_for(config: AppConfig) -> ResourceProfile:
    name = str(config.section("resources").get("profile", "balanced")).casefold()
    cpu = max(1, os.cpu_count() or 1)
    profiles = {
        "economy": ResourceProfile("economy", min(2, cpu), 1, "低功耗、低内存，适合电池供电"),
        "balanced": ResourceProfile("balanced", min(4, max(2, cpu)), 2, "笔记本默认均衡模式"),
        "performance": ResourceProfile(
            "performance", min(12, max(4, cpu * 2)), min(4, max(2, cpu // 2)), "插电全速模式"
        ),
    }
    return profiles.get(name, profiles["balanced"])


def effective_concurrency(config: AppConfig, requested: int) -> int:
    return max(1, min(int(requested), profile_for(config).concurrency_cap))


def effective_browser_pool(config: AppConfig, requested: int) -> int:
    return max(1, min(int(requested), profile_for(config).browser_pool_cap))
