"""组件发现的**唯一真源**：一个组件在磁盘上长什么样、怎么找到它。

## 为什么需要这个模块

组件目录从 `components/<id>/` 改成 `components/<分类>/<id>/` 之后，
**六处**扫描点没有跟着改，每处报的错误都不一样，我逐个撞出来：

| 位置 | 报错 | 看起来像 |
|---|---|---|
| `verify_components_runnable.py` | 「未找到任何组件配方：components」 | 目录不存在 |
| `test_heavy_wheelpack_recipes.py` | 「没找到任何带 python_abi 的配方」 | 配方缺字段 |
| `test_market_components_branch.py` | 遍历到空列表 | 市场仓是空的 |

三次都是**同一个根因、三种伪装**。而报错文案没有一个提到「扫描深度」。

## 这个模块做什么

把「组件在磁盘上的形状」收敛成**一处声明 + 一处实现**：

* `COMPONENT_RECIPE_NAME` —— 配方的文件名；
* `iter_component_recipes(root)` —— 遍历所有组件配方，**唯一正确方式**；
* `component_dir_of(recipe_path)` —— 从配方路径取组件目录。

配套门禁 `tools/gates/check_component_discovery.py` 做反向索引：
检测别处是否又出现了「自己扫 recipe.json」的裸代码。

## 用法

```python
from tools.market.component_discovery import iter_component_recipes

for recipe_path in iter_component_recipes(market_root / "components"):
    ...
```

## 支持的目录形状

**两种都支持**，因为迁移期内市场仓可能同时存在：

* `components/<id>/recipe.json`（旧扁平）
* `components/<分类>/<id>/recipe.json`（现行两级）

用 `rglob` 天然覆盖两种，不需要分支判断 —— 这也是**不要用 `glob('*/')`**
的原因：它只覆盖一层。
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

#: 组件配方的文件名。改这个等于改组件格式，属破坏性变更。
COMPONENT_RECIPE_NAME = "recipe.json"

#: 组件根目录名（相对市场仓）。
COMPONENTS_DIR_NAME = "components"


def iter_component_recipes(root: Path) -> list[Path]:
    """返回 `root` 下所有组件配方路径（已排序、去重）。

    **必须用 `rglob`**：组件按分类子目录组织
    （`components/<分类>/<id>/recipe.json`），一层 `glob` 扫不到任何东西。
    """
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob(COMPONENT_RECIPE_NAME)
        if path.is_file()
    )


def component_dir_of(recipe_path: Path) -> Path:
    """从配方路径取组件目录（配方的父目录）。"""
    return recipe_path.parent


def component_ids(root: Path) -> list[str]:
    """返回 `root` 下所有组件 id（＝配方所在目录名，已排序）。"""
    return sorted(component_dir_of(path).name for path in iter_component_recipes(root))


def iter_component_dirs(root: Path) -> Iterable[Path]:
    """遍历所有组件目录（有 `recipe.json` 的那些）。"""
    for path in iter_component_recipes(root):
        yield component_dir_of(path)
