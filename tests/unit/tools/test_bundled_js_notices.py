"""随仓分发的 JS 必须「有许可正文」——组件清单**从产物自身取证**，不靠清单文件。

## 为什么

`docs/archive/omnicrawler-evaluation-report/_shared/js/*.min.js` 会随 `docs/` 一起打进便携包
（`build_windows.ps1` 把 `docs/` 整目录拷进发布根）⇒ 这是**分发合规**问题：
MIT / Apache-2.0 / BSD-3-Clause 都要求再分发时**随附许可正文与版权声明**。

2026-09-14 登记的缺口是：「bundle 里只带各依赖的**版权行**，没有 MIT 的许可正文」；
W6.1 补上 `_shared/js/LICENSES.txt`，本文件把它变成断言。（同款先例：
`tests/unit/tools/test_bundled_font_notices.py` 用字体自身的 `name` 表核对。）

## 判定依据（怎么防止被绕过去）

1. **组件是否真的打进 bundle**：在**产物文本里**检出该组件的标识符（`cytoscape`、`dompurify`……），
   而不是读某个 manifest ⇒ 换版本、改文件名、改清单都骗不过这个检查；
2. `LICENSES.txt` 必须为**每个已检出的组件**点名，且包含其许可正文的关键句
   （MIT 的许可句 / Apache 的 "Apache License" / BSD 的 "Redistribution and use"）——
   只写一句"MIT"不算随附正文；
3. 顶层 `THIRD_PARTY_NOTICES.md` 必须声明这两个 JS 资产（防止两级声明漂移）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
JS_DIR = REPO_ROOT / "docs" / "archive" / "omnicrawler-evaluation-report" / "_shared" / "js"
LICENSES = JS_DIR / "LICENSES.txt"
NOTICES = REPO_ROOT / "THIRD_PARTY_NOTICES.md"

#: 组件 → 在产物里能检出的标识符（判定"这个组件确实被打了进去"）
_BUNDLED_MARKERS = {
    "cytoscape": "cytoscape",
    "dompurify": "dompurify",
    "katex": "katex",
    "dayjs": "dayjs",
    "dagre": "dagre",
    "marked": "marked",
}

#: 必须出现的许可正文关键句（对应各组件**实际**的许可，不是假设）
_LICENSE_SENTENCES = {
    "MIT（mermaid 及其多数依赖）": "Permission is hereby granted, free of charge",
    "Apache-2.0（echarts）": "Apache License",
    "BSD-3-Clause（zrender）": "Redistribution and use in source and binary forms",
    "ISC（d3）": "Permission to use, copy, modify, and/or distribute this software",
}


def _bundle(name: str) -> str:
    return (JS_DIR / name).read_text(encoding="utf-8", errors="replace")


def _detected_components() -> list[str]:
    """在产物文本里检出、且出现在 LICENSES.txt 里的组件名（参数化用）。"""
    hunks = _bundle("mermaid.min.js") + _bundle("echarts.min.js")
    return [name for name, marker in _BUNDLED_MARKERS.items() if marker in hunks]


def test_the_probe_is_not_vacuous() -> None:
    """先确认产物里确实检出得组件——否则下面的参数化用例会是空集对空集的假通过。"""
    detected = _detected_components()
    assert len(detected) >= 3, f"只检出 {detected}，标识符表或产物有问题"
    assert len(_bundle("mermaid.min.js")) > 100_000, "mermaid bundle 看起来不是真实产物"


def test_licenses_file_exists_and_is_substantial() -> None:
    """许可清单必须存在，且不能是「一句话交差」——至少要有完整的许可正文。"""
    assert LICENSES.is_file(), f"缺少 {LICENSES.relative_to(REPO_ROOT)}"
    text = LICENSES.read_text(encoding="utf-8")
    assert len(text) > 5_000, f"许可清单只有 {len(text)} 字符，装不下许可正文"


@pytest.mark.parametrize("component", _detected_components())
def test_every_bundled_component_is_attributed(component: str) -> None:
    """每个**在产物里检出**的组件，都必须在许可清单里被点名。"""
    text = LICENSES.read_text(encoding="utf-8")
    assert component in text, (
        f"组件 {component!r} 被打进了 bundle，却未在 `_shared/js/LICENSES.txt` 中被声明。"
        f"（判定依据是产物文本里检出了它，不是某个 manifest）"
    )


@pytest.mark.parametrize(("label", "sentence"), sorted(_LICENSE_SENTENCES.items()))
def test_license_texts_are_present(label: str, sentence: str) -> None:
    """许可**正文**（不只是名字）必须在清单里 —— 这正是 2026-09-14 登记的缺口。"""
    text = LICENSES.read_text(encoding="utf-8")
    assert sentence in text, f"{label} 的许可正文缺失（找不到 {sentence[:40]!r}…）"


def test_third_party_notices_declare_the_js_assets() -> None:
    """顶层声明必须仍然提到这两个 JS 资产与它们的许可清单（防两级声明漂移）。"""
    text = NOTICES.read_text(encoding="utf-8")
    for token in ("mermaid.min.js", "echarts.min.js", "_shared/js/LICENSES.txt"):
        assert token in text, f"THIRD_PARTY_NOTICES.md 未提到 {token}"
