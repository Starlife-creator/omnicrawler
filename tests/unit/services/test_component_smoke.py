"""组件 `smoke` 声明的守卫。

## 为什么要有这一层

早先验证脚本硬编码组件知识（`if engine == "paddle"` / `if not runner: 跳过`），
每加一个组件都要改宿主代码。现在改成组件自己声明 `smoke`，宿主只执行与核对。

这里的守卫防三类回退：

1. `smoke` 声明本身不合法（空 expect、data 类却要跑）；
2. 验证脚本重新长出**硬编码的组件知识**；
3. `expect` 为空 —— **空集合会让断言恒真**，这是最隐蔽的失效方式。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.services.component_smoke import (
    SmokeSpecError,
    parse_smoke,
    verify_result,
)

_REPO = Path(__file__).resolve().parents[3]


def _base(**overrides: object) -> dict:
    recipe: dict = {
        "kind": "wheel-pack",
        "smoke": {"payload": {}, "expect": ["ok"]},
    }
    recipe.update(overrides)
    return recipe


def test_parse_defaults() -> None:
    """基本声明能被解析。"""
    spec = parse_smoke(_base())
    assert spec.payload == {}
    assert spec.expect == ("ok",)
    assert spec.skip_run is False
    assert spec.timeout_seconds == 300.0


def test_wheel_pack_requires_smoke() -> None:
    """`kind=wheel-pack` 缺 `smoke` ⇒ 必须判红（不能靠验证器猜）。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke({"kind": "wheel-pack"})
    assert "必须声明 smoke" in str(excinfo.value)


def test_data_component_may_omit_smoke() -> None:
    """`kind=data` 可省略 `smoke`，等价于 skip_run。"""
    spec = parse_smoke({"kind": "data"})
    assert spec.skip_run is True
    assert spec.expect == ()


def test_empty_expect_is_rejected() -> None:
    """**空 expect 必须判红** —— 空集合会让所有断言恒真。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke(_base(smoke={"payload": {}, "expect": []}))
    assert "不能为空" in str(excinfo.value)


def test_data_cannot_skip_run_false() -> None:
    """`kind=data` 却写 `skip_run: false` ⇒ 判红（它没有可执行入口）。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke(_base(kind="data", smoke={"expect": ["ok"]}))
    assert "skip_run" in str(excinfo.value)


def test_wheel_pack_cannot_skip_run() -> None:
    """`kind=wheel-pack` 却 `skip_run: true` ⇒ 判红（必须真的执行一次）。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke(_base(smoke={"skip_run": True}))
    assert "skip_run" in str(excinfo.value)


def test_unknown_smoke_key_rejected() -> None:
    """`smoke` 不许有未声明的键（防「写了但没人读」）。"""
    with pytest.raises(SmokeSpecError) as excinfo:
        parse_smoke(_base(smoke={"expect": ["ok"], "wat": 1}))
    assert "不允许的键" in str(excinfo.value)


def test_verify_result_checks_expect() -> None:
    """`expect` 里的键必须存在且为真。"""
    spec = parse_smoke(_base(smoke={"expect": ["ok", "engine"]}))
    assert verify_result(spec, {"ok": True, "engine": "paddle"}) == []

    problems = verify_result(spec, {"ok": True})
    assert any("engine" in item for item in problems)

    problems = verify_result(spec, {"ok": True, "engine": ""})
    assert any("值为假" in item for item in problems)


def test_verify_result_surfaces_stage_and_error() -> None:
    """组件自报失败时，要把 stage + error 带出来（不是只说「失败」）。"""
    spec = parse_smoke(_base())
    problems = verify_result(
        spec, {"ok": False, "stage": "import_component", "error": "ImportError: boom"}
    )
    assert problems
    assert "import_component" in problems[0]
    assert "ImportError: boom" in problems[0]


def test_verify_script_is_data_driven() -> None:
    """验证脚本**不得重新长出硬编码的组件知识**。

    早先是 `if engine == "paddle"` / `if not runner: 跳过`。
    现在必须读 `smoke`，新增组件时不需要改脚本。
    """
    script = _REPO / "tools" / "portable" / "verify_standard_components.py"
    if not script.is_file():
        pytest.skip("需要 verify_standard_components.py")

    source = script.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # 这两处是旧的「宿主猜类型」标志
    assert 'engine == "paddle"' not in code, "脚本里仍有硬编码的 engine 判断"
    assert "_parse_smoke(" in code, "脚本没有读 smoke 声明"
    assert "verify_result(" in code, "脚本没有核对 smoke.expect"


def test_market_components_declare_smoke() -> None:
    """市场里每个组件都必须能解析出 `smoke`。"""
    market = _REPO.parent / "OmniCrawler-market" / "components"
    if not market.is_dir():
        pytest.skip("需要 market 仓")

    checked = 0
    problems: list[str] = []
    for recipe_path in sorted(market.glob("*/recipe.json")):
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
        checked += 1
        try:
            parse_smoke(data)
        except SmokeSpecError as exc:
            problems.append(f"{recipe_path.parent.name}: {exc}")
    assert checked > 0, "没有可检查的组件"
    assert not problems, f"smoke 声明不合法：{problems}"
