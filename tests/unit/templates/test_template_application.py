"""`templates/template_application.py` 的守卫：**模板不得翻转安全方向**。

## 为什么这个模块值得测（此前 0% 覆盖）

`apply_template()` 把模板段合并进用户配置。有一条安全底线：

```python
_restore_safe_baseline(after, before)
```

它把 `http` / `egress` 里的安全键**回盖成用户既有值** —— 也就是说，
**模板不能把 `verify_tls: true` 改成 `false`**，也不能把 `allow_private_networks`
从 `false` 改成 `true`。

这条判据防的是真实的攻击面：模板来自市场、由第三方发布。若模板能翻转
安全方向，一个恶意模板就能悄悄关掉 TLS 校验或放开内网访问，而用户只
看到「应用了一个模板」。

本测试固定三件事：
1. 安全键**不被模板覆盖**；
2. 非安全键**正常合并**（不能因为保安全就整体跳过）；
3. `undo()` 真的还原到应用前状态（用户要能退回去）。

## 边界

不验模板语法/渲染 —— 那是 `recipe_engine` 与模板校验门禁的事。
"""
from __future__ import annotations

import pytest

from omnicrawler.templates.template_application import (
    apply_template,
)


def _current() -> dict:
    """用户既有配置：安全方向是收紧的。"""
    return {
        "http": {
            "timeout": 10,
            "verify_tls": True,
            "allow_private_network": False,
            "respect_robots": True,
            "max_redirects": 5,
        },
        # 安全键的真实位置是 `http.allow_private_network`（单数）——
        # 见 `_SAFE_HTTP_KEYS`。写成 egress 段或其他拼写都不会被回盖。
        "http_extra": {},
        "egress": {
            "enabled": True,
        },
        "source": {"name": "我的站点"},
    }


def test_template_cannot_disable_tls_verification() -> None:
    """**核心安全守卫**：模板想把 verify_tls 改成 False ⇒ 必须被回盖成 True。"""
    template = {"http": {"verify_tls": False, "timeout": 30}}
    result = apply_template(_current(), template)
    assert result.after["http"]["verify_tls"] is True, (
        "模板关掉了 TLS 校验 —— 安全键没有被回盖"
    )
    # 非安全键应当正常合并
    assert result.after["http"]["timeout"] == 30


def test_template_cannot_open_private_network() -> None:
    """模板想把 `allow_private_network` 改成 True ⇒ 必须被回盖成 False。"""
    template = {"http": {"allow_private_network": True}}
    result = apply_template(_current(), template)
    assert result.after["http"]["allow_private_network"] is False, (
        "模板放开了内网访问 —— 安全键没有被回盖"
    )


def test_template_cannot_disable_robots() -> None:
    """模板想把 `respect_robots` 改成 False ⇒ 必须被回盖成 True。"""
    template = {"http": {"respect_robots": False}}
    result = apply_template(_current(), template)
    assert result.after["http"]["respect_robots"] is True


def test_template_cannot_disable_egress() -> None:
    """模板想把 egress.enabled 改成 False ⇒ 必须被回盖（出网开关是安全键）。"""
    template = {"egress": {"enabled": False}}
    result = apply_template(_current(), template)
    assert result.after["egress"]["enabled"] is True


def test_non_safe_sections_are_merged() -> None:
    """非安全段必须真的合并 —— 不能因为保安全就整体跳过模板。"""
    template = {"http": {"timeout": 60}, "source": {"name": "新站点", "extra": 1}}
    result = apply_template(_current(), template)
    assert result.after["http"]["timeout"] == 60
    assert result.after["source"]["name"] == "新站点"
    assert result.after["source"]["extra"] == 1


def test_unspecified_and_unknown_fields_are_preserved() -> None:
    """模板没提到的字段、以及用户自定义字段，都必须保留。"""
    current = _current()
    current["source"]["custom_field"] = "用户自己加的"
    result = apply_template(current, {"http": {"timeout": 20}})
    assert result.after["source"]["custom_field"] == "用户自己加的"
    assert result.after["http"]["max_redirects"] == 5


def test_only_selected_sections_are_applied() -> None:
    """显式给 sections 时，只应用这些段。"""
    template = {"http": {"timeout": 99}, "egress": {"allow_private_networks": True}}
    result = apply_template(_current(), template, sections=["egress"])
    # 只选了 egress，所以 http.timeout 不该变
    assert result.after["http"]["timeout"] == 10
    assert result.applied_sections == ("egress",)


def test_undo_restores_exact_previous_state() -> None:
    """`undo()` 必须还原到应用前 —— 用户要能退回去。"""
    current = _current()
    original = {
        "http": dict(current["http"]),
        # 安全键的真实位置是 `http.allow_private_network`（单数）——
        # 见 `_SAFE_HTTP_KEYS`。写成 egress 段或其他拼写都不会被回盖。
        "http_extra": {},
        "egress": {
            "enabled": True,
        },
        "source": {"name": "我的站点"},
    }
    result = apply_template(current, {"http": {"timeout": 999}})
    assert result.undo() == original


def test_undo_does_not_alias_the_original() -> None:
    """`undo()` 返回副本：改它不该影响原配置（deepcopy 语义）。"""
    result = apply_template(_current(), {"http": {"timeout": 999}})
    restored = result.undo()
    restored["http"]["timeout"] = -1
    assert result.before["http"]["timeout"] == 10


def test_changes_have_business_labels() -> None:
    """变更条目必须带中文学段名与变更类型（UI 直接展示）。"""
    result = apply_template(_current(), {"http": {"timeout": 30}})
    assert result.changes, "有变更却没有记录 changes"
    for item in result.changes:
        assert "business_section" in item
        assert item.get("business_change") in ("新增", "修改", "移除")


def test_template_mutation_does_not_leak_into_result() -> None:
    """模板对象被改动不该影响已生成的结果（deepcopy 语义）。"""
    template = {"http": {"timeout": 30}}
    result = apply_template(_current(), template)
    template["http"]["timeout"] = 9999
    assert result.after["http"]["timeout"] == 30


@pytest.mark.parametrize("sections", [None, [], ["http"]])
def test_apply_template_is_idempotent(sections) -> None:
    """对同一输入连用两次，第二次不该再产生变更（幂等）。"""
    current = _current()
    template = {"http": {"timeout": 30}}
    first = apply_template(current, template, sections=sections)
    second = apply_template(first.after, template, sections=sections)
    assert second.after == first.after
    assert not second.changes, f"重复应用产生了多余变更：{second.changes}"
