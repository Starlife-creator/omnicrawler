"""点分 `provides` 必须落到**域前缀槽位**，而不是建出幽灵槽位。

## 事故形态（2026-11-27 实测）

市场 16 个组件的 `provides` 全是 `ocr.<name>`，而旧实现把**整串**当槽位名，
于是建出 `ocr.paddle` / `ocr.convert-markdown` / … 共 16 个槽位，
**而消费方查的是 `slot("ocr")`** ⇒ 16 个组件一个都看不见。

不报错、不红测试，只是「注册成功了但没人查得到」。
"""

from __future__ import annotations

from omnicrawler.core.capability_slots import CapabilitySlotRegistry, build_builtin_slot_registry


def test_dotted_provides_lands_in_the_domain_slot() -> None:
    """★ 核心判据：`ocr.paddle` 的槽位是 `ocr`，不是 `ocr.paddle`。"""
    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}], source="component"
    )
    slot = registry.slot("ocr")
    assert slot is not None, "点分名没有落到 ocr 槽位"
    providers = slot.providers()
    assert [item.provider_id for item in providers] == ["ocr-paddle"]


def test_no_ghost_slot_is_created() -> None:
    """★ 反方向：不许再建出 `ocr.paddle` 这种幽灵槽位。"""
    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(
        [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}], source="component"
    )
    assert registry.slot("ocr.paddle") is None, "又建出了幽灵槽位"


def test_full_capability_name_is_kept_in_metadata() -> None:
    """完整能力名要留在 metadata 里 —— 一个槽位下多个 provider 时靠它分辨。"""
    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(
        [{"id": "convert-markdown", "provides": ["ocr.convert-markdown"]}],
        source="component",
    )
    provider = registry.slot("ocr").get("convert-markdown")
    assert provider is not None
    assert provider.metadata.get("capability") == "ocr.convert-markdown"


def test_bare_slot_name_still_works() -> None:
    """裸名（`"ocr"`）仍是它自己 —— 不能因为修点分名把这条弄坏。"""
    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(
        [{"id": "legacy-ocr", "provides": ["ocr"]}], source="plugin"
    )
    assert registry.slot("ocr").get("legacy-ocr") is not None


def test_dict_form_slot_is_not_split() -> None:
    """★ 字典形态是扩展语法：`{"slot": "ocr"}` 里的 slot 就是槽位名，不拆分。"""
    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(
        [{"id": "smart-ocr", "provides": [{"slot": "ocr", "priority": 140}]}],
        source="plugin",
    )
    provider = registry.slot("ocr").get("smart-ocr")
    assert provider is not None and provider.priority == 140


def test_all_market_components_land_in_one_slot() -> None:
    """★ 用**真实市场配方**验证：16 个组件都该进 ocr 槽位。

    这条是端到端的：读 `../OmniCrawler-market/components/*/*/recipe.json`。
    市场仓不在时 skip（CI 上由 checkout_market 提供）。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "components"
    if not market.is_dir():
        import pytest

        pytest.skip("没有市场 checkout")

    # ★ 必须走 `iter_component_recipes()`：组件按 `components/<分类>/<id>/` 组织，
    #   自己 glob 会`扫到空且报错伪装成「未找到任何组件配方」。
    #   门禁 `check_component_discovery` 守着这条（本次就是它抓到的）。
    from tools.market.component_discovery import iter_component_recipes

    entries = []
    for recipe in iter_component_recipes(market.parent):
        data = json.loads(recipe.read_text(encoding="utf-8"))
        entries.append({"id": data["id"], "provides": data.get("provides") or []})
    assert len(entries) >= 16, f"市场组件数偏少：{len(entries)}"

    registry = CapabilitySlotRegistry()
    registry.register_manifest_entries(entries, source="component")
    ocr_slot = registry.slot("ocr")
    assert ocr_slot is not None, "组件没能落到 ocr 槽位"
    assert len(ocr_slot.providers()) == len(entries), (
        f"只有 {len(ocr_slot.providers())}/{len(entries)} 个组件进了 ocr 槽位"
    )


def test_builtin_ocr_engines_still_win_on_priority() -> None:
    """★ 回归：组件不能抢走内置 OCR 引擎的首选位（priority 120 vs 110）。"""
    registry = build_builtin_slot_registry()
    registry.register_manifest_entries(
        [{"id": "convert-markdown", "provides": ["ocr.convert-markdown"]}],
        source="component",
    )
    active = registry.slot("ocr").active()
    assert active is not None
    assert active.provider_id in {"paddleocr", "tesseract"}, (
        f"组件抢走了 OCR 首选位：{active.provider_id}"
    )
