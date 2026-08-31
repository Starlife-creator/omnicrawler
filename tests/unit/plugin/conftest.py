from __future__ import annotations

import os
import sys
from pathlib import Path


def _market_tools() -> Path:
    configured = os.environ.get("OMNICRAWL_MARKET_TOOLS", "").strip()
    if configured:
        candidate = Path(configured).resolve()
        if (candidate / "validate_submission.py").is_file():
            return candidate
        raise RuntimeError("OMNICRAWL_MARKET_TOOLS 未指向有效的 market/tools")
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "OmniCrawler-market" / "tools"
        if (candidate / "validate_submission.py").is_file():
            return candidate
    raise RuntimeError("测试需要相邻的 OmniCrawler-market checkout")


MARKET_TOOLS = _market_tools()
if str(MARKET_TOOLS) not in sys.path:
    sys.path.insert(0, str(MARKET_TOOLS))
