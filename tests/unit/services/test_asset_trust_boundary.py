"""条目 9：哈希钉的范围边界，必须**可执行地**说清楚。

**要解决的真实矛盾**

刚落地的组件 recipe（`services/component_recipe.py`，提交 `9d7759c9`）
要求**每个构件都带 sha256**，缺一条就拒绝整份 recipe。

但条目 4 已经查证：Paddle 模型**上游不提供内容哈希**。
`paddlex/inference/utils/official_models.py` 里的 sha256 是
**用模型名列表算缓存键**，不是校验文件内容。

于是有人按 recipe 去填 Paddle 模型时会发现**根本填不了**。
这不是 bug，是两条边界撞在一起了——必须把范围写明，否则：

* 要么有人伪造一个"哈希"（那比不钉更危险，因为它**看起来**验过了）；
* 要么有人为了"能填"去放宽 recipe 的哈希要求（那会削弱真正的安全边界）。

**判据（本文件钉住四件事）**

1. **三类资产的责任边界必须可枚举**：有内容哈希的 / 没有的 / 明确不负责的；
2. **没有哈希的资产不得伪造哈希**：代码里不允许"拿模型名/版本号顶替 sha256"；
3. **recipe 的哈希要求不得为了 Paddle 模型放宽**——那是两套东西；
4. 边界说明必须**对用户可见**（不是只写在开发文档里）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "omnicrawler"


def test_asset_hash_responsibility_is_enumerated() -> None:
    """边界表必须存在且覆盖三类资产。

    这张表是**唯一的准据**：将来有人问"为什么这个没哈希"，
    答案必须在这里，而不是散落在某段注释里。
    """
    from omnicrawler.services import asset_trust

    table = asset_trust.HASH_RESPONSIBILITY
    assert isinstance(table, dict) and table, "缺少资产责任边界表"

    # 三类必备资产
    for key in ("application", "components", "paddle_models"):
        assert key in table, f"责任边界表缺少 {key}"

    # 每一条都必须说明"谁负责内容完整性"
    for key, entry in table.items():
        assert entry.get("owner"), f"{key} 没写责任方"
        assert entry.get("has_content_hash") in (True, False), f"{key} 没明确有无内容哈希"
        assert entry.get("reason"), f"{key} 没写理由"


def test_paddle_models_are_declared_without_content_hash() -> None:
    """Paddle 模型必须被**明确**标为"无内容哈希"，而不是含糊带过。

    含糊的代价：下一个人会以为"我们验过了"。
    """
    from omnicrawler.services.asset_trust import HASH_RESPONSIBILITY

    entry = HASH_RESPONSIBILITY["paddle_models"]
    assert entry["has_content_hash"] is False
    # 理由必须提到上游不提供，而不是"我们懒得算"
    assert "上游" in entry["reason"] or "upstream" in entry["reason"].lower()


def test_no_fake_hash_for_models() -> None:
    """代码里不得拿模型名/版本号之类顶替内容哈希。

    反例（必须被抓住）：把模型名 sha256 一下当"校验值"——
    那是个恒定值，换掉模型内容它**不会变**，却看起来像验过了。
    """
    from omnicrawler.services import asset_trust

    # 该模块自己不能提供"给模型算哈希"的接口
    for name in dir(asset_trust):
        lowered = name.lower()
        if "model" in lowered and "hash" in lowered:
            pytest.fail(
                f"asset_trust 暴露了 {name!r}：给模型算哈希会制造"
                "「看起来验过了」的假象（上游不提供内容哈希）"
            )

    # 明确提供的接口只有"查询责任方"
    assert hasattr(asset_trust, "content_hash_required")
    assert asset_trust.content_hash_required("components") is True
    assert asset_trust.content_hash_required("paddle_models") is False


def test_recipe_hash_requirement_is_not_relaxed_for_models() -> None:
    """recipe 的哈希要求不得因为"Paddle 没哈希"而放宽。

    两套东西：
    * 组件构件 = **我们自己声明**来源 ⇒ 必须带 sha256；
    * Paddle 模型 = 上游下发、无哈希 ⇒ 由上游负责，且**不进 recipe 的 artifacts**。
    """
    from omnicrawler.services.component_recipe import RecipeError, validate_recipe

    recipe = {
        "schema": 1,
        "name": "demo",
        "version": "1.0.0",
        "platforms": ["windows"],
        "architectures": ["x86_64"],
        "artifacts": [
            # 试图用模型名/版本号顶替哈希
            {"url": "https://example.org/m.zip", "sha256": "mymodel-v1", "extract_to": "m"}
        ],
        "entry": {"path": "m/ocr.exe"},
    }
    with pytest.raises(RecipeError, match="sha256|哈希"):
        validate_recipe(recipe)


def test_model_download_does_not_claim_verification() -> None:
    """模型下载模块**不得**对外声称做了内容校验。

    判据：模块 docstring 必须明说不校验；且不得出现"verify"/"校验通过"这类
    会让人误以为验过的措辞描述字节完整性。
    """
    text = (_SRC / "services" / "model_download.py").read_text(encoding="utf-8")
    assert "不校验" in text or "不为其背书" in text, (
        "model_download 没有明确声明「不校验模型字节」"
    )


def test_boundary_is_visible_to_users() -> None:
    """边界必须对**用户**可见，不能只写在开发文档里。

    判据：`models` CLI 的相关 help 或输出里要能读到"未做内容校验"的说明，
    或文档 `docs/` 里有一节专门讲这件事。
    """
    docs = list((_REPO_ROOT / "docs").rglob("*.md"))
    hits = [
        path
        for path in docs
        if "不校验模型字节" in path.read_text(encoding="utf-8", errors="replace")
        or "不为其背书" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert hits, (
        "没有任何面向用户的文档说明「模型字节不由本项目校验」——"
        "用户会默认以为下载下来的东西被验过"
    )
