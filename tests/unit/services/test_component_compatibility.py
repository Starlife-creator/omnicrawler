"""`check_compatibility()` 的 ABI / 平台 / 架构判定。

## 为什么需要这些守卫

真实 CI（v1.0.7）暴露了一个**自相矛盾**的判定：

```
组件装配失败：组件要求的 Python ABI 与当前不符：需要 ['cp313']，当前 cp313
```

需要的和当前的是**同一个 ABI**，却被判成不符。根因是：

```python
_require(str(required_abi) == target_abi, ...)
```

`python_abi` 在 recipe 里是**列表**（一份 recipe 可声明多个可接受的 ABI），
`str(["cp313"])` 得到字符串 `"['cp313']"`，与 `"cp313"` 永不相等。

后果：**任何声明了 `python_abi` 的配方都装不上**——包括完全匹配的情况。
这类缺陷不会在"只跑静态门禁"时暴露，只有真的执行安装才看得见。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.component_recipe import check_compatibility


def _recipe(**overrides) -> dict:
    base = {
        "platforms": ["windows", "linux", "macos"],
        "architectures": ["x86_64"],
        "python_abi": ["cp313"],
    }
    base.update(overrides)
    return base


def test_matching_abi_is_accepted() -> None:
    """声明的 ABI 与当前相同时必须通过。

    这是那个真实缺陷的直接复现：列表 `["cp313"]` vs 字符串 `"cp313"`。
    """
    check_compatibility(_recipe(), platform="windows", architecture="x86_64", abi="cp313")


def test_abi_list_with_multiple_entries() -> None:
    """多个可接受 ABI 时任取其一即可。"""
    recipe = _recipe(python_abi=["cp312", "cp313", "cp314"])
    check_compatibility(recipe, platform="linux", architecture="x86_64", abi="cp313")


def test_non_matching_abi_is_rejected() -> None:
    """真正不匹配时必须拒绝——修复不能把判据改成恒真。"""
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(python_abi=["cp312"]),
            platform="windows",
            architecture="x86_64",
            abi="cp313",
        )
    assert "ABI" in str(excinfo.value)


def test_abi_absent_means_no_constraint() -> None:
    """没声明 `python_abi` 时不施加约束（纯 Python 组件）。"""
    recipe = {"platforms": ["linux"], "architectures": ["x86_64"]}
    check_compatibility(recipe, platform="linux", architecture="x86_64", abi="cp313")


def test_platform_mismatch_is_rejected() -> None:
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(platforms=["windows"]),
            platform="linux",
            architecture="x86_64",
            abi="cp313",
        )
    assert "平台" in str(excinfo.value)


def test_architecture_mismatch_is_rejected() -> None:
    with pytest.raises(Exception) as excinfo:
        check_compatibility(
            _recipe(architectures=["aarch64"]),
            platform="linux",
            architecture="x86_64",
            abi="cp313",
        )
    assert "架构" in str(excinfo.value)


@pytest.mark.parametrize("abi_form", [["cp313"], ("cp313",)])
def test_abi_accepts_list_and_tuple(abi_form) -> None:
    """列表与元组两种写法都要支持。"""
    check_compatibility(
        _recipe(python_abi=abi_form),
        platform="linux",
        architecture="x86_64",
        abi="cp313",
    )


def test_abi_string_form_is_tolerated() -> None:
    """单个字符串写法也接受（旧配方可能这么写）。"""
    check_compatibility(
        _recipe(python_abi="cp313"),
        platform="linux",
        architecture="x86_64",
        abi="cp313",
    )


def test_market_recipes_are_installable_on_this_interpreter() -> None:
    """**市场里的真实配方**必须能在当前解释器上通过兼容性检查。

    这条用真实数据：只测构造的 dict 发现不了"配方里的写法与解析器的假设不符"。
    """
    import json
    from pathlib import Path

    market = Path(__file__).resolve().parents[4] / "OmniCrawler-market"
    components = market / "components"
    if not components.is_dir():
        pytest.skip("需要 market 仓的 components 目录")

    from omnicrawler.services.component_recipe import current_abi, current_architecture, current_platform

    checked = 0
    for recipe_path in sorted(components.glob("*/recipe.json")):
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
        abi = recipe.get("python_abi")
        if not abi:
            continue  # 纯数据构件（如语言包）不声明 ABI
        checked += 1
        check_compatibility(
            recipe,
            platform=current_platform(),
            architecture=current_architecture(),
            abi=current_abi(),
        )
    if checked == 0:
        pytest.skip("当前没有声明 python_abi 的配方")
