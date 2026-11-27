#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""组件 `provides` 的**域前缀必须与组件职责一致**（`ocr.` 是保留前缀）。

## 为什么需要这道门

2026-11-27 实测：市场 16 个组件里只有 **5 个**真在做 OCR，其余 11 个
（转 Markdown、表格、导出 docx/xlsx、抽取正文、解析 feed、去重、定时）
全部声明成 `ocr.*` —— 历史上它们都挂在「OCR 后处理」这个名义下。

**后果不是名字难看**：主仓 `capability_slots` 按**域前缀**建能力槽位，
`ocr.` 一统天下意味着这 11 个组件和真正的 OCR 引擎**抢同一个槽位**；
CI 上内置引擎不可用时，`slot("ocr").active()` 真的选中了 `convert-markdown`
—— 一个根本不做 OCR 的东西（这条在 CI 上真实红过）。

## 判据

`ocr.` 是**保留前缀**：只有真 OCR 引擎（`_OCR_COMPONENT_IDS`）能声明 `ocr.*`。
其余组件必须用自己的域（`convert.` / `doc.` / `extract.` / `parse.` /
`dedupe.` / `schedule.` / `text.`）。

## 与市场侧的关系

市场仓 `tools/check_component_recipes._check_provides` 有同一条判据（发布门禁）。
**两边都要有**：市场侧挡「发布一个占错域的组件」，主仓侧挡「主仓自己写错 /
市场数据被回滚」。只改一边等于没改。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.market.component_discovery import iter_component_recipes  # noqa: E402

#: 真 OCR 引擎的组件 id；与市场仓 `_OCR_COMPONENT_IDS` **同集合**。
_OCR_COMPONENT_IDS = frozenset({
    "ocr-paddle",
    "ocr-rapidocr",
    "ocr-easyocr",
    "ocr-doctr",
    "tesseract-chi-sim",
})


def market_root() -> Path:
    """市场仓根目录（与 `check_component_recipes` 的默认 `--root` 同源）。"""
    return _ROOT.parent / "OmniCrawler-market"


def violations(recipes: list[Path]) -> list[str]:
    """返回违规说明列表（空 = 通过）。"""
    problems: list[str] = []
    checked = 0
    for recipe in recipes:
        try:
            data = json.loads(recipe.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"{recipe}: 读不了配方（{exc}）")
            continue
        component_id = str(data.get("id") or recipe.parent.name)
        provides = data.get("provides") or []
        if not isinstance(provides, list):
            problems.append(f"{component_id}: provides 必须是列表")
            continue
        checked += 1
        for item in provides:
            if not isinstance(item, str):
                continue
            capability = item.strip().lower()
            if capability.startswith("ocr.") and component_id not in _OCR_COMPONENT_IDS:
                problems.append(
                    f"{component_id}: 声明了 {capability!r} 但不是 OCR 引擎组件；"
                    "`ocr.` 是保留前缀，只给真 OCR 引擎 —— 否则它会和 OCR 引擎"
                    "抢同一个能力槽位（CI 上真实发生过）"
                )
    if checked == 0:
        # ★ 空集合判红：扫到 0 份配方时，「没有违规」是恒真的假象。
        problems.append("没有检查任何组件配方（空集合不是通过）")
    return problems


def main(argv: list[str] | None = None) -> int:
    del argv
    root = market_root()
    if not root.is_dir():
        print(f"[SKIP] 没有市场 checkout：{root}")
        return 0
    recipes = iter_component_recipes(root / "components")
    problems = violations(recipes)
    if problems:
        print(f"[FAIL] 组件能力域前缀违规（{len(problems)} 处）：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"[OK] {len(recipes)} 份组件配方的 provides 域前缀都与组件职责一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
