"""`check_component_capability_domains` 门禁：`ocr.*` 是保留前缀。

## 它守什么

2026-11-27 实测：市场 16 个组件里只有 5 个真在做 OCR，其余 11 个全声明 `ocr.*`
（历史上都挂在「OCR 后处理」名义下）。主仓按**域前缀**建能力槽位 ⇒ 它们和 OCR
引擎抢同一个槽位；CI 上内置引擎不可用时 `slot("ocr").active()` 真的选中了
`convert-markdown` —— 一个根本不做 OCR 的东西。

已用**真正的创作者钥匙**重签并改到各自的域；这条门禁防止它再漂回去。
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.gates.check_component_capability_domains import (
    _OCR_COMPONENT_IDS,
    main,
    market_root,
    violations,
)


def _recipe(tmp_path: Path, component_id: str, provides: list[str]) -> Path:
    directory = tmp_path / "components" / "cat" / component_id
    directory.mkdir(parents=True)
    path = directory / "recipe.json"
    path.write_text(
        json.dumps({"id": component_id, "provides": provides}), encoding="utf-8"
    )
    return path


def test_ocr_engine_may_declare_ocr_namespace(tmp_path: Path) -> None:
    """正方向：真 OCR 引擎**可以**声明 `ocr.*`。"""
    assert violations([_recipe(tmp_path, "ocr-paddle", ["ocr.paddle"])]) == []


def test_non_ocr_component_may_not_declare_ocr_namespace(tmp_path: Path) -> None:
    """★ 核心判据：转 Markdown 的组件不许声明 `ocr.*`。"""
    problems = violations([_recipe(tmp_path, "convert-markdown", ["ocr.convert-markdown"])])
    assert len(problems) == 1
    assert "保留前缀" in problems[0] and "convert-markdown" in problems[0]


def test_non_ocr_domain_passes_for_non_ocr_component(tmp_path: Path) -> None:
    assert violations([_recipe(tmp_path, "convert-markdown", ["convert.markdown"])]) == []


def test_all_real_ocr_ids_are_recognised(tmp_path: Path) -> None:
    """`_OCR_COMPONENT_IDS` 里的每个 id 都该被放行（防常量写错一半）。"""
    assert len(_OCR_COMPONENT_IDS) == 5
    for index, name in enumerate(sorted(_OCR_COMPONENT_IDS)):
        directory = tmp_path / f"c{index}"
        assert violations([_recipe(directory, name, ["ocr.anything"])]) == [], name


def test_empty_recipe_list_is_red() -> None:
    """★ 空集合判红：扫到 0 份配方时「没有违规」是恒真的假象。"""
    problems = violations([])
    assert problems and "空集合不是通过" in problems[0]


def test_malformed_recipe_is_reported_not_silently_skipped(tmp_path: Path) -> None:
    """读不了配方要报出来 —— 静默跳过会让「0 违规」变成假象。"""
    path = tmp_path / "bad" / "recipe.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    problems = violations([path])
    assert problems and "读不了配方" in problems[0]


def test_main_is_green_on_the_real_market() -> None:
    """端到端：真实市场仓应当通过（没有市场 checkout 时 skip）。"""
    import pytest

    if not market_root().is_dir():
        pytest.skip("没有市场 checkout")
    assert main([]) == 0
