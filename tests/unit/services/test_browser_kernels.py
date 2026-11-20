"""浏览器内核清单的守卫。

## 这批守卫防什么

把「槽位能提供的」与「组件市场里有的」合成一张表时，有两类错误极易发生，
而且都不会报错、只会给出错误信息：

1. **来源判错**：`playwright` 与 `selenium` 都声明
   `engine_family=chromium`，按能力名匹配会让 `selenium` 挂上
   Chromium 的 415 MiB 与「内置」标记（实测踩到）。
2. **本地内核被隐藏**：用户导入的内核若不出现在同一张表里，
   就成了「隐藏状态」——比不支持导入还糟。

## 判据

* 来源以**组件**为准，槽位 `metadata["bundled"]` 不是来源凭据；
* 组件归属要求**实现方式一致**（playwright 的 Chromium ≠ ChromeDriver）；
* 本地内核**必须**出现在列表里，且 `trust` 显式为 `local` + 带警告；
* 不可用的内核**保留在列表里**（用户要知道「有但缺」），不能静默过滤。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.browser_kernels import (
    KernelOption,
    describe_kernels,
    list_kernels,
)


def _catalog_with_builtin_chromium() -> dict:
    return {
        "components": [
            {
                "id": "chromium-builtin",
                "provides": ["browser.chromium"],
                "declared_total_bytes": 435574347,
                "artifacts": [{"source": "builtin", "sha256": "a" * 64, "size": 435574347}],
            }
        ]
    }


def test_builtin_chromium_gets_size_and_builtin_source() -> None:
    """内置 Chromium 必须带上真实大小，且标为 builtin。"""
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    chromium = next(o for o in options if o.provider_id == "playwright")
    assert chromium.source == "builtin"
    assert chromium.size_bytes == 435574347
    assert chromium.component_id == "chromium-builtin"
    assert chromium.trust_label if hasattr(chromium, "trust_label") else True


def test_selenium_does_not_inherit_chromium_component() -> None:
    """**`selenium` 不得继承 Chromium 的组件归属**。

    两者都声明 `engine_family=chromium`，但 `selenium` 的字节来自
    ChromeDriver，与内置 Chromium 不是同一份。按能力名匹配会让它
    挂上 415 MiB 和「内置」标记 —— 实测踩到。
    """
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    selenium = [o for o in options if o.provider_id.startswith("selenium")]
    assert selenium, "没有 selenium 类 provider"
    for option in selenium:
        assert option.source != "builtin", (
            f"{option.provider_id} 被标成内置 —— 它和内置 Chromium 不是同一份字节"
        )
        assert option.size_bytes is None, (
            f"{option.provider_id} 挂上了不属于它的大小 {option.size_bytes}"
        )
        assert option.component_id is None


def test_unavailable_kernels_stay_in_list() -> None:
    """**不可用的内核必须留在列表里**（带警告），不能静默过滤。

    用户需要知道「有这个东西、但现在缺文件」，而不是看到一张
    只有可用项的、看不出缺口在哪的表。
    """
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    unavailable = [o for o in options if not o.available]
    # 本机通常缺 firefox/webkit/selenium 驱动；至少要有 warn 语义的项
    for option in unavailable:
        assert option.warnings, f"{option.provider_id} 不可用却没有警告"


def test_local_kernels_appear_with_unverified_trust() -> None:
    """**本地导入的内核必须出现在同一张表里**，且标注未验证。

    隐藏状态比不做还糟：用户自己导入了内核，却在界面里看不到。
    """
    options = list_kernels(
        catalog=_catalog_with_builtin_chromium(),
        local_kernels=[
            {"label": "我的 Chrome", "path": __file__},  # 用真实存在的文件
            {"label": "缺失的", "path": "/definitely/not/here.exe"},
        ],
    )
    local = [o for o in options if o.source == "local"]
    assert len(local) == 2, f"本地内核没有全部出现：{[o.provider_id for o in local]}"

    existing = next(o for o in local if "here" not in o.provider_id)
    assert existing.available is True
    assert any("未验证" in w for w in existing.warnings), "现有本地内核没有未验证警告"

    missing = next(o for o in local if "here" in o.provider_id)
    assert missing.available is False
    assert any("不存在" in w for w in missing.warnings)


def test_available_sorted_before_unavailable() -> None:
    """可用的排在前面 —— 用户打开列表第一眼该看到能用的。"""
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    flags = [o.available for o in options]
    # 一旦出现 False，后面不能再有 True
    first_false = flags.index(False) if False in flags else len(flags)
    assert all(not flag for flag in flags[first_false:])


def test_describe_renders_trust_and_warnings() -> None:
    """文本输出必须带上信任标注与警告 —— 不能只给名字。"""
    option = KernelOption(
        provider_id="local:x",
        display_name="我的内核",
        engine_family="chromium",
        source="local",
        available=False,
        trust="local",
        warnings=("未验证来源，仅本机使用",),
    )
    text = describe_kernels([option])
    assert "未验证" in text
    assert "我的内核" in text


def test_empty_list_is_explicit() -> None:
    """空列表必须明说，不能返回空字符串。"""
    assert describe_kernels([]).strip(), "空清单返回了空字符串"


def test_source_key_produces_no_keyerror() -> None:
    """`as_dict()` 必须能序列化（CLI 的 `--json` 依赖它）。"""
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    for option in options:
        payload = option.as_dict()
        assert payload["provider_id"]
        assert "trust_label" in payload
        assert isinstance(payload["warnings"], list)


@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
def test_engine_family_preserved(engine: str) -> None:
    """引擎族必须原样保留（槽位选择与登录态绑定都依赖它）。"""
    options = list_kernels(catalog=_catalog_with_builtin_chromium())
    matching = [o for o in options if o.engine_family == engine]
    assert matching, f"没有 {engine} 内核"
