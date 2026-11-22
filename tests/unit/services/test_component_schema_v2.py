"""Schema v2（`requires` / `platform_matrix`）的守卫。

## 这批守卫防什么

v1 有两个结构性缺口（§15.8）：

1. **配方表达不了依赖图**。依赖只有 `deps.lock.json` 这份旁路快照，
   配方本身看不出「我需要什么」。而 pip 存在的理由就是「手写依赖必然漏」——
   本轮实测就漏过 `httpx` / `httpcore` / `networkx` / `protobuf` / `safetensors`。
2. **`platform_architectures` 表达不了「同平台不同架构用不同构件/ABI」**。
   `paddlepaddle 3.3.1` 的 macOS 轮子只有 arm64 —— 这是真实情况，
   不是理论问题。

## 关键设计：两版并存，但**不允许双写**

迁移期 v1 配方继续可用；v1 的 `platform_architectures` 与 v2 的
`platform_matrix` **不得同时出现**。双写必然漂移，这是本轮反复踩的那类问题。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.component_recipe import (
    SUPPORTED_SCHEMA,
    RecipeError,
    _validate_platform_matrix,
    _validate_requires,
    load_recipe,
)
from tools.market.component_discovery import iter_component_recipes


def _matrix_recipe() -> dict:
    return {
        "schema": 2,
        "platforms": ["windows", "macos"],
        "architectures": ["aarch64", "x86_64"],
        "artifacts": [{"sha256": "a" * 64}, {"sha256": "b" * 64}],
        "platform_matrix": {
            # Windows 有 x86_64；macOS 只有 arm64（paddle 的真实情况）
            "windows": {"x86_64": {"abi": ["cp313"], "artifacts": [0]}},
            "macos": {"aarch64": {"abi": ["cp313"], "artifacts": [1]}},
        },
    }

def test_schema_accepts_both_versions() -> None:
    """v1 与 v2 都必须被接受（迁移期不强制升级）。"""
    assert 1 in SUPPORTED_SCHEMA
    assert 2 in SUPPORTED_SCHEMA

def test_platform_matrix_accepts_partial_arch() -> None:
    """**一个平台只支持部分架构必须合法** —— paddle 的 macOS 只有 arm64。"""
    _validate_platform_matrix(_matrix_recipe())

def test_platform_matrix_and_legacy_are_mutually_exclusive() -> None:
    """`platform_matrix` 与 `platform_architectures` **不得同时出现**。

    双写必然漂移：一处改了另一处忘改，就变成「声明与现实脱节」。
    """
    recipe = _matrix_recipe()
    recipe["platform_architectures"] = {"windows": ["x86_64"]}
    with pytest.raises(RecipeError) as excinfo:
        _validate_platform_matrix(recipe)
    assert "不能同时出现" in str(excinfo.value)

def test_platform_matrix_union_must_match_global() -> None:
    """全局 `architectures` 必须等于 matrix 的并集（防两处漂移）。"""
    recipe = _matrix_recipe()
    recipe["architectures"] = ["x86_64"]  # 漏了 aarch64
    with pytest.raises(RecipeError) as excinfo:
        _validate_platform_matrix(recipe)
    assert "不一致" in str(excinfo.value)

def test_platform_matrix_rejects_unknown_platform() -> None:
    """matrix 不得引入未在 `platforms` 里声明的平台。"""
    recipe = _matrix_recipe()
    recipe["platform_matrix"]["android"] = {"aarch64": {"artifacts": [0]}}
    with pytest.raises(RecipeError) as excinfo:
        _validate_platform_matrix(recipe)
    assert "未声明的平台" in str(excinfo.value)

def test_platform_matrix_rejects_out_of_range_artifact_index() -> None:
    """构件下标越界必须判红 —— 否则装配时会去索引不存在的构件。"""
    recipe = _matrix_recipe()
    recipe["platform_matrix"]["windows"]["x86_64"]["artifacts"] = [99]
    with pytest.raises(RecipeError) as excinfo:
        _validate_platform_matrix(recipe)
    assert "下标" in str(excinfo.value)

def test_platform_matrix_rejects_unknown_detail_key() -> None:
    """明细只允许 `abi` / `artifacts`，其余键一律拒绝。"""
    recipe = _matrix_recipe()
    recipe["platform_matrix"]["windows"]["x86_64"]["post_install"] = "rm -rf /"
    with pytest.raises(RecipeError) as excinfo:
        _validate_platform_matrix(recipe)
    assert "不允许的键" in str(excinfo.value)

def test_requires_accepts_list() -> None:
    """`requires` 支持包名列表。"""
    _validate_requires({"requires": ["paddlepaddle", "opencv-contrib-python"]})

def test_requires_accepts_mapping() -> None:
    """`requires` 支持 `{包名: 版本约束}`。"""
    _validate_requires(
        {"requires": {"paddlepaddle": ">=3.0", "opencv-contrib-python": ">=4"}}
    )

def test_requires_rejects_empty() -> None:
    """空 `requires` 会让「依赖都已声明」恒真 —— 必须判红。"""
    with pytest.raises(RecipeError):
        _validate_requires({"requires": []})
    with pytest.raises(RecipeError):
        _validate_requires({"requires": {}})

@pytest.mark.parametrize(
    "value",
    [
        ["foo_bar", "foo-bar"],  # 归一后重复
        ["../etc/passwd"],  # 不是包名
        ["pkg; rm -rf /"],  # 注入形态
        [123],  # 非字符串
    ],
)
def test_requires_rejects_invalid(value: object) -> None:
    """非法包名/重复/non-string 全部拒绝。"""
    with pytest.raises(RecipeError):
        _validate_requires({"requires": value})

def test_requires_is_optional() -> None:
    """`requires` 可缺省（v1 配方没有它）。"""
    _validate_requires({})  # 不抛异常即通过

def test_existing_market_recipes_still_load() -> None:
    """既有的 v1 配方必须继续能加载（向后兼容）。"""
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market" / "components"
    if not market.is_dir():
        pytest.skip("需要市场仓 components 目录")

    recipes = iter_component_recipes(market)
    assert recipes, "没有找到任何配方（扫描本身失效）"
    for path in recipes:
        loaded = load_recipe(path)
        assert loaded["name"]
