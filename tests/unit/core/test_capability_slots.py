"""Unit tests for CapabilitySlot & equal-citizen builtin/plugin/component providers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.capabilities import (
    CapabilityProvider,
    CapabilitySlot,
    build_builtin_slot_registry,
    default_capability_slots,
    discover_installed_providers,
)


def test_builtin_plugin_and_component_compete_on_equal_footing() -> None:
    slot = CapabilitySlot("ocr", "OCR 引擎")
    builtin = CapabilityProvider(
        provider_id="tesseract",
        slot_name="ocr",
        source="builtin",
        display_name="Tesseract",
        priority=100,
    )
    component = CapabilityProvider(
        provider_id="tesseract-chi-sim",
        slot_name="ocr",
        source="component",
        display_name="Tesseract 简体中文组件",
        priority=115,
    )
    plugin = CapabilityProvider(
        provider_id="custom-vlm-ocr",
        slot_name="ocr",
        source="plugin",
        display_name="自定义视觉 OCR 插件",
        priority=130,
    )
    slot.register(builtin)
    slot.register(component)
    slot.register(plugin)

    # 默认按优先级择优，插件/组件/内置在同一套规则下平等竞争
    active = slot.active()
    assert active is not None and active.provider_id == "custom-vlm-ocr"
    assert [p.source for p in slot.available()] == ["plugin", "component", "builtin"]

    # 用户在业务界面就地切换为指定组件后，active 立即精确命中该组件
    slot.set_preferred("tesseract-chi-sim")
    chosen = slot.active()
    assert chosen is not None and chosen.provider_id == "tesseract-chi-sim"

    # 调用级显式参数优先于槽位默认偏好；切回 auto 恢复优先级排序
    override = slot.active("tesseract")
    assert override is not None and override.provider_id == "tesseract"
    slot.set_preferred("auto")
    assert slot.active() == plugin


def test_unavailable_provider_is_skipped_and_strict_mode_does_not_mask_missing() -> None:
    slot = CapabilitySlot("browser", "浏览器引擎")
    slot.register(
        CapabilityProvider(
            provider_id="missing-browser",
            slot_name="browser",
            source="plugin",
            display_name="未就绪插件浏览器",
            priority=200,
            availability_check=lambda: False,
        )
    )
    slot.register(
        CapabilityProvider(
            provider_id="playwright",
            slot_name="browser",
            source="builtin",
            display_name="Playwright",
            priority=100,
            availability_check=lambda: True,
        )
    )

    # 非严格模式自动回退至可用提供者；严格模式如实返回 None 以便界面提示缺失
    fallback = slot.active("missing-browser", strict=False)
    assert fallback is not None and fallback.provider_id == "playwright"
    assert slot.active("missing-browser", strict=True) is None


def test_slot_rejects_mismatched_slot_or_invalid_source() -> None:
    slot = CapabilitySlot("html_parser")
    wrong = CapabilityProvider(
        provider_id="playwright",
        slot_name="browser",
        source="builtin",
        display_name="Playwright",
    )
    with pytest.raises(ValueError, match="不一致"):
        slot.register(wrong)

    with pytest.raises(ValueError, match="未知提供者来源"):
        CapabilityProvider(
            provider_id="x",
            slot_name="html_parser",
            source=" privileged ",  # type: ignore[arg-type]
            display_name="Invalid",
        )


def test_discover_installed_providers_loads_both_plugins_and_components(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins_installed"
    (plugins_dir / "smart-ocr").mkdir(parents=True)
    (plugins_dir / "smart-ocr" / "plugin.json").write_text(
        json.dumps(
            {
                "id": "smart-ocr",
                "title": "Smart OCR Plugin",
                "provides": [{"slot": "ocr", "priority": 140}],
            }
        ),
        encoding="utf-8",
    )

    comp_manifest = tmp_path / "components" / "installed.json"
    comp_manifest.parent.mkdir(parents=True)
    comp_manifest.write_text(
        json.dumps(
            {
                "tesseract-chi-sim": {
                    "name": "tesseract-chi-sim",
                    "purpose": "中文离线识别包",
                    "runtime": {"protocol": "ocr-file-v1", "engine": "tesseract"},
                }
            }
        ),
        encoding="utf-8",
    )

    reg = build_builtin_slot_registry()
    discover_installed_providers(
        plugins_dir=plugins_dir,
        components_manifest=comp_manifest,
        registry=reg,
    )
    ocr_slot = reg.slot("ocr")
    assert ocr_slot is not None
    ids = {p.provider_id: p.source for p in ocr_slot.providers()}
    assert ids["smart-ocr"] == "plugin"
    assert ids["tesseract-chi-sim"] == "component"
    assert ids["paddleocr"] == "builtin"
    assert default_capability_slots().slot("html_parser") is not None
