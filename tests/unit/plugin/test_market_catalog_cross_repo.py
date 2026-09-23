"""主仓 market/catalog.json（内置快照）与市场仓 catalog.json 的 templates 数组一致性。

背景（2026-09-23 审计）：``tools/sync_snapshot.py`` 只做镜像、无断言；
快照漂移（市场仓改了 templates、主仓忘了同步）会让离线内置快照与真源分叉，
而离线发行版用的正是这份快照（``core/config.py`` bundled_catalog_dir="market"）。

判据边界：
- **templates 数组**必须逐项相等（这是本断言的守卫对象）；
- **plugins 数组**不比对 —— 历史上已分叉（主仓快照只收 example_news，
  市场仓已发布 7 个插件），收口插件快照是独立事项，不混入本守卫；
- ``generated_at`` / ``sequence`` / ``official_publishers`` 是发布元数据，不比对。
- 市场仓缺席时整组跳过（与 test_branding_cross_repo 相同的两仓同级约定）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKET_ROOT = REPO_ROOT.parent / "OmniCrawler-market"

pytestmark = pytest.mark.skipif(
    not (MARKET_ROOT / "catalog.json").is_file(),
    reason="市场仓未 clone（两仓同级约定）",
)


def test_snapshot_templates_match_market_repo() -> None:
    main_catalog = json.loads((REPO_ROOT / "market" / "catalog.json").read_text(encoding="utf-8"))
    market_catalog = json.loads((MARKET_ROOT / "catalog.json").read_text(encoding="utf-8"))
    main_templates = main_catalog.get("templates") or []
    market_templates = market_catalog.get("templates") or []

    main_ids = [item.get("id") for item in main_templates]
    market_ids = [item.get("id") for item in market_templates]
    assert main_ids == market_ids, (
        "主仓快照与市场仓的模板 id 集合不一致：\n"
        f"主仓独有: {sorted(set(main_ids) - set(market_ids))}\n"
        f"市场仓独有: {sorted(set(market_ids) - set(main_ids))}\n"
        "（请运行 tools/sync_snapshot.py 重新镜像，或检查是否漏发布）"
    )
    assert main_templates == market_templates, (
        "模板 id 一致但条目内容漂移（summary/license/versions 等字段），需重新同步快照"
    )
