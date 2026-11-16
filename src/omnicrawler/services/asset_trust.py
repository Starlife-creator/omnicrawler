"""资产信任边界：**哪些东西有内容哈希、由谁负责**（条目 9）。

为什么需要一张表
----------------
本项目分发三类资产，它们的完整性责任**刻意不同**：

| 资产 | 来源 | 有内容哈希吗 | 谁负责完整性 |
|---|---|---|---|
| 应用本体（便携包） | 本项目构建 | 有（构建期签名 + 清单哈希） | 本项目 |
| 组件（`.ocp`） | 可由第三方出，经签名 | **有**（清单逐文件 sha256） | 签名者 + 本项目验签 |
| Paddle 模型 | **上游**（PaddleX 自行下载） | **没有** | 上游 |

不把这张表写下来，会出现两种错误，**而且两种都比"不钉"更糟**：

1. **伪造一个哈希**。曾设想过"拿模型名列表算个 sha256"——那是个恒定值，
   模型内容换了它**不会变**，却让界面/日志看起来像"已校验"。
   `paddlex/inference/utils/official_models.py` 里的 sha256 正是这种：
   它算的是**缓存键**，不是文件内容。照抄它当校验值 = 假装验过。
2. **为了"能填"放宽真正的边界**。组件 recipe 要求每个构件带 sha256
   （见 `services/component_recipe.py`）。若因为 Paddle 模型没有哈希
   就去放宽这条要求，那削弱的是**真正受保护的那部分**。

所以边界必须**可枚举、可执行、对用户可见**——这就是本模块。

为什么不做"让用户自己钉"
------------------------
`核心优化`里列过这个选项。它有真实价值，但**不是现在**：先要有稳定的
资产清单格式与界面入口。在机制齐备前提供它，只会变成一个新的半成品。

实现约束
--------
本模块**刻意不提供**任何"为模型计算哈希"的接口——见上面第 1 条的教训。
它只回答一个问题：**这份资产，内容哈希是不是必需的？**
"""
from __future__ import annotations

from typing import Any

#: 资产类型 → 责任边界。
#:
#: ``has_content_hash`` 是**事实描述**（上游有没有提供），不是"我们要不要校验"。
HASH_RESPONSIBILITY: dict[str, dict[str, Any]] = {
    "application": {
        "owner": "本项目（构建与发布）",
        "has_content_hash": True,
        "mechanism": "便携包清单逐文件 sha256 + 发布签名（configs/update_trust.pub.pem）",
        "reason": "应用本体由本项目构建与分发，内容完整性完全由本项目负责",
    },
    "components": {
        "owner": "组件签名者（可能是第三方）+ 本项目验签",
        "has_content_hash": True,
        "mechanism": "component.json 的 files 逐文件 sha256 + ed25519 签名"
                     "（configs/component_trust.pub.pem）",
        "reason": "组件构件由清单声明来源，必须能证明「这份清单没被改过」，"
                  "否则签名形同虚设",
    },
    "paddle_models": {
        "owner": "上游（PaddleX / BOS / HuggingFace / ModelScope）",
        "has_content_hash": False,
        "mechanism": "无。由 PaddleX 按模型名下载到本地缓存，本项目不托管、不镜像",
        "reason": "上游不提供模型文件的内容哈希——PaddleX 里那串 sha256 是用"
                  "**模型名列表**算的缓存键，不是文件内容校验值。"
                  "本项目的立场是：内容完整性由上游负责，本项目不为其背书，"
                  "因此**不伪造哈希**（伪造会让「看起来验过了」比「没验」更危险）",
    },
}

#: 可以随组件 recipe 分发的资产类型（即：必须带 sha256 的那些）。
#:
#: Paddle 模型**不在**此列：它们不进 recipe 的 artifacts，由 PaddleX 自己拉。
RECIPE_ELIGIBLE = frozenset({"components", "application"})


def content_hash_required(asset: str) -> bool:
    """该资产的内容哈希是否为**必需品**。

    用于装配/校验路径的分支判断：必需时必须校验，不必须时**不得假装校验**。
    """
    entry = HASH_RESPONSIBILITY.get(asset)
    if entry is None:
        raise KeyError(
            f"未知资产类型：{asset!r}；已知：{sorted(HASH_RESPONSIBILITY)}"
        )
    return bool(entry["has_content_hash"])


def responsibility(asset: str) -> dict[str, Any]:
    """返回该资产的完整责任边界（供界面/诊断展示）。"""
    entry = HASH_RESPONSIBILITY.get(asset)
    if entry is None:
        raise KeyError(
            f"未知资产类型：{asset!r}；已知：{sorted(HASH_RESPONSIBILITY)}"
        )
    return dict(entry)


def explain(asset: str) -> str:
    """一句话说明该资产"为什么有/没有内容哈希"，可直接展示给用户。"""
    entry = responsibility(asset)
    verdict = "有内容哈希" if entry["has_content_hash"] else "无内容哈希"
    return f"{asset}：{verdict}。责任方 {entry['owner']}。{entry['reason']}"


def unverified_assets() -> list[str]:
    """当前**没有**内容哈希的资产清单。

    用途：在下载/装配界面上明确告知用户"这些不由我们校验"，
    而不是让用户默认以为下载下来的东西都被验过。
    """
    return sorted(
        name for name, entry in HASH_RESPONSIBILITY.items()
        if not entry["has_content_hash"]
    )
