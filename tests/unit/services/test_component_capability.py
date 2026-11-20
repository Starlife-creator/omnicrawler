"""插件 ↔ 组件能力契约的守卫。

## 这批守卫防什么

市场里原本是两条平行线：插件声明 `required_capabilities`（我要什么），
组件声明 `provides`（我能给什么），**没有任何代码把它们对上**。
结果是用户装了插件、没装组件时，只在运行期撞 `ModuleNotFoundError`。

## 最容易犯的错：把宿主核心能力也算进去

插件的能力名里绝大多数（`records.page` / `view.richtext` / `state.get` /
`surface.background.*`）是**宿主 SDK 提供**的，与组件无关。
拿它们去和组件的 `provides` 比对，会得出「市场里 5 个插件都缺能力」这种
**错误结论** —— 我第一次跑就撞上了。

所以本模块只判**组件式能力**（`ocr.*` / `pdf.*` / `browser.*` / `model.*`）。
下面第一条守卫就钉这个划分。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.services.component_capability import (
    COMPONENT_CAPABILITY_DOMAINS,
    component_provisions,
    install_hint,
    is_component_capability,
    resolve,
)

_REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "name",
    ["ocr.paddle", "ocr.tesseract", "pdf.render", "browser.chromium", "model.llm"],
)
def test_component_capabilities_recognised(name: str) -> None:
    assert is_component_capability(name)


@pytest.mark.parametrize(
    "name",
    [
        "records.page",
        "view.richtext",
        "state.get",
        "surface.background.set",
        "artifact.stream.write",
        "network.fetch",
    ],
)
def test_core_capabilities_excluded(name: str) -> None:
    """宿主核心能力**不得**参与组件匹配。

    否则会把「核心能力正常」误报成「市场里一半插件废了」。
    """
    assert not is_component_capability(name)


def test_domains_are_declared_not_scattered() -> None:
    """能力域前缀必须是**一个常量**，而不是散落在各处 if 条件里。"""
    assert COMPONENT_CAPABILITY_DOMAINS, "能力域列表为空"
    assert all(d.endswith(".") for d in COMPONENT_CAPABILITY_DOMAINS), (
        "域前缀必须以点结尾，否则 `ocrodemo.x` 会被误判成 `ocr.*`"
    )


def test_resolve_satisfied_when_component_provides() -> None:
    """组件声明了该能力 ⇒ 插件满足。"""
    plugins = [{"id": "p", "required_capabilities": {"ocr.paddle": ">=1"}}]
    components = [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}]
    report = resolve(plugins, components)
    assert report.ok
    check = report.checks[0]
    assert check.satisfied_by["ocr.paddle"] == ["ocr-paddle"]
    assert not check.missing


def test_resolve_missing_when_no_component() -> None:
    """没有组件提供 ⇒ 必须报缺（这是「装了插件跑不起来」的本地前置拦截）。"""
    plugins = [{"id": "p", "required_capabilities": {"ocr.paddle": ">=1"}}]
    components = [{"id": "other", "provides": ["pdf.render"]}]
    report = resolve(plugins, components)
    assert not report.ok
    assert report.checks[0].missing == ["ocr.paddle"]


def test_core_requirements_do_not_cause_failure() -> None:
    """只有核心能力的插件必须判**满足**（哪怕一个组件都没装）。"""
    plugins = [
        {
            "id": "community-guide",
            "required_capabilities": {"view.richtext": ">=1", "records.page": ">=1"},
        }
    ]
    report = resolve(plugins, [])
    assert report.ok, "核心能力被误算成组件能力"
    assert report.checks[0].required == []


def test_empty_provides_means_nothing() -> None:
    """空 `provides` 的组件**不提供任何能力**（不是「全满足」）。"""
    provided = component_provisions([{"id": "x", "provides": []}])
    assert provided == {}


def test_capability_names_case_insensitive() -> None:
    """能力名大小写不敏感（`OCR.Paddle` ≡ `ocr.paddle`）。"""
    plugins = [{"id": "p", "required_capabilities": {"OCR.Paddle": ">=1"}}]
    components = [{"id": "c", "provides": ["Ocr.Paddle"]}]
    assert resolve(plugins, components).ok


def test_install_hint_names_the_component() -> None:
    """缺能力时提示必须**指名组件**，让用户知道装什么。"""
    plugins = [{"id": "p", "required_capabilities": {"ocr.paddle": ">=1"}}]
    components = [{"id": "ocr-paddle", "provides": ["ocr.paddle"]}]
    # 构造「有组件但没装」的场景：resolve 时不给组件
    report = resolve(plugins, [])
    hint = install_hint(report.checks[0], components)
    assert "ocr-paddle" in hint, f"提示没有指名组件：{hint}"


def test_install_hint_admits_when_nothing_provides() -> None:
    """市场里根本没有组件提供时，如实说「暂无」，不编一个。"""
    plugins = [{"id": "p", "required_capabilities": {"ocr.paddle": ">=1"}}]
    report = resolve(plugins, [])
    hint = install_hint(report.checks[0], [])
    assert "暂无组件提供" in hint


def test_real_market_is_consistent() -> None:
    """真实 catalog：所有插件的**组件式**能力都必须被某个组件覆盖。

    这条用真实数据，防止「市场发布了一个注定跑不起来的插件组合」。
    """
    catalog = _REPO / "market" / "catalog.json"
    if not catalog.is_file():
        pytest.skip("需要 market/catalog.json")

    data = json.loads(catalog.read_text(encoding="utf-8"))
    report = resolve(data.get("plugins") or [], data.get("components") or [])
    assert report.ok, (
        "这些插件的组件式能力无人提供："
        + "; ".join(f"{c.plugin_id}: {c.missing}" for c in report.broken)
    )


def test_real_market_components_declare_provides() -> None:
    """真实 catalog：每个组件都必须导出 `provides`（catalog 层也要有）。"""
    catalog = _REPO / "market" / "catalog.json"
    if not catalog.is_file():
        pytest.skip("需要 market/catalog.json")

    data = json.loads(catalog.read_text(encoding="utf-8"))
    missing = [
        component.get("id")
        for component in data.get("components") or []
        if not component.get("provides")
    ]
    assert not missing, f"这些组件在 catalog 里没有 provides：{missing}"
