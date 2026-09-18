"""AI 的 ``config_patch``：**要么接上，要么如实说**（走查 R4.4 路线 B）。

## 背景（R4.4 取证，2026-09-18）

AI 输出里的 ``config_patch`` 曾经**经安全校验后从未被应用**：全仓唯一消费点是
``validate_task_config_safety``，它只用来**拦截**；``draft.config_patch`` 此后再无任何读取。
但取证发现的真问题比"校验完丢弃"更根本 —— **prompt 契约在向模型索要产品接不住的东西**：

10 个键里 **6 个能接到 ``QuickTaskDraft``、4 个接不上**（``download_extensions`` /
``topic_filter`` 只存在配置层；``schedule`` 草稿侧是 ``str`` 而 patch 侧是 ``dict``；
``seed_urls`` 无独立承载位）。⇒ 白花 token 与延迟，而「校验通过」让用户以为改动已落地。

处置（路线 B「契约对齐」）：**能接的全接上**、**接不上的不再要**、**仍返回的如实说**。

★ 本文件钉住两个方向，缺一个就是新的失真：

1. **不静默**：撤回的键若仍被返回，必须出现在 ``warnings`` 里并给出**真实**的手动去处；
2. **不撒谎**：措辞是「建议未应用」，**不是**「产品做不到」——
   配置层确实有 ``download.extensions`` / ``topic.include_any``，写成"做不到"就是
   R4.1 刚钉过的「把既有能力说成没有」。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import omnicrawler.core.config as config_module
from omnicrawler.services import natural_language_task as nlt
from omnicrawler.services.ai_task_designer import (
    CONFIG_PATCH_ROUTED,
    CONFIG_PATCH_SECURITY_ONLY,
    CONFIG_PATCH_WITHDRAWN,
    config_patch_keys_in_prompt,
    validate_task_config_safety,
)
from omnicrawler.services.natural_language_task import compile_natural_language, compile_with_ai


class _FakeProvider:
    """最小 provider：把 ``config_patch`` 包进合法 JSON 返回，不触网。"""

    name = "fake"
    model = "fake-1"

    def __init__(self, patch: dict[str, Any]) -> None:
        self._text = json.dumps(
            {
                "known_requirements": {
                    "url": "https://example.com/a",
                    "intent": "save_page",
                    "topics": [],
                },
                "assumptions": [],
                "unresolved_questions": [],
                "config_patch": patch,
                "explanations": [],
                "risks": [],
                "recommended_actions": [],
            },
            ensure_ascii=False,
        )

    def generate(self, messages, **kwargs):  # noqa: ANN001, ANN003 - 与 provider 契约一致
        return type("R", (), {"text": self._text})()


#: 6 个接线键的**行为用例**。★ 与 ``CONFIG_PATCH_ROUTED`` 的一致性由
#: `test_every_routed_key_has_a_behavioural_case` 直接断言 —— 新增键却忘了接线
#: （或忘了给用例）会红，而不是悄悄变成"声明了但没接"。
_ROUTED_CASES: tuple[tuple[dict[str, Any], str, object], ...] = (
    ({"task_intent": "monitor_changes"}, "intent", "monitor_changes"),
    ({"source_kind": "crawl"}, "source_kind", "crawl"),
    ({"max_pages": 77}, "max_pages", 77),
    ({"process_pdf": True}, "process_pdf", True),
    ({"monitor_same_url": True}, "monitor_changes", True),
    ({"output_formats": ["csv"]}, "output_formats", ("csv",)),
)


# ── 1. 契约：prompt 只索要产品接得住的东西 ──────────────────────────────


def test_prompt_example_is_valid_json() -> None:
    """示例本身必须是合法 JSON —— 我们正是在教模型输出严格 JSON，示例先坏掉更荒谬。"""
    keys = config_patch_keys_in_prompt()
    assert keys, "一个键都没取到：后面的断言会变成空对空的假通过"


def test_prompt_only_asks_for_carried_keys() -> None:
    """prompt 里每个 ``config_patch`` 键都必须有承载位或被安全拦截器消费。

    ★ 索要了却接不住 = 白花 token + 「校验通过」的假象，正是 R4.4 的病根。
    """
    carried = set(CONFIG_PATCH_ROUTED) | set(CONFIG_PATCH_SECURITY_ONLY)
    orphans = sorted(set(config_patch_keys_in_prompt()) - carried)
    assert not orphans, f"prompt 索要了产品接不住的键：{orphans}"


def test_prompt_no_longer_asks_for_withdrawn_keys() -> None:
    """撤回的键不得再出现在 prompt 里（否则模型会继续为它们花 token）。"""
    still_there = sorted(set(config_patch_keys_in_prompt()) & set(CONFIG_PATCH_WITHDRAWN))
    assert not still_there, f"这些键已撤回却还留在 prompt 示例里：{still_there}"


def test_the_three_registries_are_disjoint() -> None:
    """每个键**恰好**归一类：既"能接"又"撤回"= 对同一件事自相矛盾。"""
    routed = set(CONFIG_PATCH_ROUTED)
    security = set(CONFIG_PATCH_SECURITY_ONLY)
    withdrawn = set(CONFIG_PATCH_WITHDRAWN)
    assert routed & security == set()
    assert routed & withdrawn == set()
    assert security & withdrawn == set()


# ── 2. 接线：AI 给了值 ⇒ 草稿上就有值 ───────────────────────────────────


@pytest.mark.parametrize(("patch", "attribute", "expected"), _ROUTED_CASES)
def test_routed_keys_reach_the_draft(
    patch: dict[str, Any], attribute: str, expected: object
) -> None:
    draft = compile_with_ai("保存 https://example.com/a", _FakeProvider(patch))
    assert getattr(draft.task, attribute) == expected


def test_every_routed_key_has_a_behavioural_case() -> None:
    """★ 完整性：``CONFIG_PATCH_ROUTED`` 里每个键都必须被行为用例覆盖。"""
    covered = {key for patch, _, _ in _ROUTED_CASES for key in patch}
    assert covered == set(CONFIG_PATCH_ROUTED), (
        f"缺用例的键：{sorted(set(CONFIG_PATCH_ROUTED) - covered)}；"
        f"表里没有的键：{sorted(covered - set(CONFIG_PATCH_ROUTED))}"
    )


def test_intent_change_recomputes_the_intent_defaults() -> None:
    """意图从"保存单页"变成"栏目"后，页数默认值必须跟着变 —— 否则确认单自相矛盾。"""
    draft = compile_with_ai(
        "保存 https://example.com/a", _FakeProvider({"task_intent": "collect_section"})
    )
    assert draft.task.intent == "collect_section"
    assert draft.task.max_pages == 30, "意图变了、默认值没跟着变（`max_pages` 还是单页的 1）"
    assert draft.task.source_kind == "crawl"


def test_the_request_wins_over_the_patch() -> None:
    """需求原文是用户自己的话 ⇒ 它优先于 AI 的建议（两条路径共用同一处语义层）。"""
    draft = compile_with_ai(
        "抓取 https://example.com/a 的全部 50 页标题", _FakeProvider({"max_pages": 7})
    )
    assert draft.task.max_pages == 50


def test_empty_patch_leaves_the_draft_untouched() -> None:
    """空 patch 不得改变任何东西 —— 同一条需求"开不开 AI"必须一致（R4.1 的不变量）。"""
    enhanced = compile_with_ai("保存 https://example.com/a", _FakeProvider({})).task
    assert enhanced.warnings == ()
    assert enhanced == compile_natural_language("保存 https://example.com/a").task


# ── 3. 不静默：撤回的键与非法取值都要说出来 ─────────────────────────────


@pytest.mark.parametrize("key", sorted(CONFIG_PATCH_WITHDRAWN))
def test_withdrawn_keys_returned_anyway_are_reported(key: str) -> None:
    """模型仍返回撤回键（自由 JSON 拦不住）⇒ 如实告警 + **真实**手动去处。"""
    draft = compile_with_ai("保存 https://example.com/a", _FakeProvider({key: ["x"]}))
    text = "".join(draft.task.warnings)
    assert key in text, f"{key} 被静默丢弃了"
    assert CONFIG_PATCH_WITHDRAWN[key] in text, "没给出手动去处"
    assert draft.task.confirmation()["提醒"] == list(draft.task.warnings), "确认单没带上提醒"


@pytest.mark.parametrize("key", sorted(CONFIG_PATCH_WITHDRAWN))
def test_withdrawn_wording_does_not_deny_the_capability(key: str) -> None:
    """★ 不撒谎：措辞必须是「建议未应用」，不能是「产品做不到」。

    配置层确实有 ``download.extensions`` / ``topic.include_any`` —— 写成"做不到"
    就是 R4.1 刚钉过的「把既有能力说成没有」。
    """
    draft = compile_with_ai("保存 https://example.com/a", _FakeProvider({key: ["x"]}))
    text = "".join(draft.task.warnings)
    for denial in ("做不到", "没有对等能力", "不支持"):
        assert denial not in text, f"告警把既有能力说成了没有（出现「{denial}」）：{text}"


def test_invalid_values_are_reported_once_not_swallowed() -> None:
    """非法取值汇总成**一条**告警：既不逐项刷屏，也不假装没看见。"""
    draft = compile_with_ai(
        "保存 https://example.com/a",
        _FakeProvider({"max_pages": 0, "source_kind": "nope", "process_pdf": "yes"}),
    )
    text = "".join(draft.task.warnings)
    assert "不合法" in text
    for fragment in ("max_pages=0", "source_kind='nope'", "process_pdf='yes'"):
        assert fragment in text, f"{fragment} 没出现在告警里"
    # 非法值不得落到草稿上（`False`/`0` 是草稿默认值，说明确实没接）
    assert draft.task.max_pages == 1
    assert draft.task.source_kind == "static_html"
    assert draft.task.process_pdf is False


def test_a_clean_patch_produces_no_warning_noise() -> None:
    """反向：patch 合法且无撤回键时不许凭空产生提醒（否则提醒会被当噪声忽略）。"""
    draft = compile_with_ai(
        "保存 https://example.com/a", _FakeProvider({"max_pages": 5, "source_kind": "crawl"})
    )
    assert draft.task.warnings == ()


# ── 4. `seed_urls` 仍由安全拦截器消费（不是"静默丢弃"） ─────────────────


def test_seed_urls_is_consumed_by_the_safety_gate() -> None:
    """``seed_urls`` 没有承载位，但它的用途是**发现越界** ⇒ 留在 prompt 里是对的。

    ★ 这不算静默丢弃：入口范围由需求决定，AI 提的 seed 要么与入口一致（无需改动），
    要么越界（会被拦截器**明确拦下**）—— 两者用户都看得见。
    """
    assert "seed_urls" in config_patch_keys_in_prompt()
    assert "seed_urls" in CONFIG_PATCH_SECURITY_ONLY
    violations = validate_task_config_safety(
        {"seed_urls": ["https://evil.example.org/x"]}, allowed_domains=["https://example.com/a"]
    )
    assert violations, "越界的 seed_urls 没被拦下 —— 那它才真的成了'提了没人看'"
    assert "evil.example.org" in "".join(violations)


# ── 5. 两处口径不许漂移 ────────────────────────────────────────────────


def test_page_bounds_match_the_config_validator() -> None:
    """草稿层页数界必须与 `core/config.py` 的 `crawl.max_pages` 校验一致。

    放宽/收紧都会让草稿与配置校验各说各话：草稿放行、配置拒绝（或反之）。
    """
    source = Path(config_module.__file__).read_text(encoding="utf-8")
    match = re.search(r'\("max_pages",\s*(\d[\d_]*),\s*(\d[\d_]*)\)', source)
    assert match, "没在 core/config.py 里找到 crawl.max_pages 的界（判据失去锚点，直接判红）"
    assert int(match.group(1).replace("_", "")) == nlt.MIN_TASK_PAGES
    assert int(match.group(2).replace("_", "")) == nlt.MAX_TASK_PAGES
