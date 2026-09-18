"""需求语义必须被承载、且做不到的必须说出来（走查 R4.1）。

## 背景（0.13.0 实测，`.audit-tmp/w71/`）

把需求原文喂给 `compile_natural_language` 时，**字段清单**、「按价格从低到高排序」、
「统计每个用户的帖子数」、「点击年份 2015 等待 AJAX」全部丢失，且 `warnings` 为空。
而 `QuickTaskDraft.confirmation()` 却把「字段内容」列为可修改项 —— 即**声明了但未接线**：
需求语义没有承载位置，解析结果只能被丢掉。

本文件钉住三件事：

1. **承载**：字段清单 / 后处理 / 输出格式 / 页数真的进到草稿里，且确认单不与自己矛盾；
2. **不静默**：凡进 `unsupported` 的名称，都能在 `warnings` 里找到**一个去处**；
3. **不撒谎**：已经能做的事（R3.2 的自动滚动、既有的 `compare-runs`）不得被写成"做不到"。

★ 第 3 条是这一批最容易犯的错 —— 把「不自动发生」笼统说成「没有对等能力」，
在滚动与 compare-runs 两处都会变成假话。
"""

from __future__ import annotations

import pytest

from omnicrawler.services import natural_language_task as nlt
from omnicrawler.services.natural_language_task import compile_natural_language, compile_with_ai

_AI_JSON = (
    '{"known_requirements": {"url": "https://books.toscrape.com", "intent": "collect_section", '
    '"topics": []}, "assumptions": [], "unresolved_questions": [], "explanations": [], '
    '"risks": [], "recommended_actions": [], "config_patch": {}}'
)


class _FakeProvider:
    """最小 provider：固定返回一段合法 JSON，不触网。"""

    name = "fake"
    model = "fake-1"

    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, messages, **kwargs):  # noqa: ANN001, ANN003 - 与 provider 契约一致
        return type("R", (), {"text": self._text})()


# ── 1. 承载：需求里的语义真的进到草稿里 ─────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "expected_fields"),
    [
        (
            "抓取 https://books.toscrape.com 的全部 50 页书籍的标题、价格和库存状态",
            ("标题", "价格", "库存状态"),
        ),
        ("采集 https://example.com/list 的 书名、作者、出版社", ("书名", "作者", "出版社")),
    ],
)
def test_requested_fields_are_carried(demand: str, expected_fields: tuple[str, ...]) -> None:
    task = compile_natural_language(demand).task
    assert task.fields == expected_fields


def test_field_list_reaches_the_confirmation_sheet() -> None:
    """确认单把「字段内容」列为**可修改项**，那它就必须有真值可改（R4.1 的核心）。"""
    confirmation = compile_natural_language(
        "抓取 https://books.toscrape.com 的标题、价格"
    ).task.confirmation()
    assert confirmation["字段内容"] == ["标题", "价格"]
    assert confirmation["字段来源"] == "需求指定"
    assert "字段内容" in confirmation["可修改"]

    auto = compile_natural_language("保存 https://example.com/x").task.confirmation()
    assert auto["字段内容"] == []
    assert auto["字段来源"] == "自动推断"


def test_page_count_is_not_contradicted_by_the_decision_lines() -> None:
    """页数取自需求后，决策行里的默认页数必须同步 —— 否则确认单自相矛盾。"""
    task = compile_natural_language("抓取 https://books.toscrape.com 的全部 50 页书籍的标题、价格").task
    assert task.max_pages == 50
    decisions = "".join(task.decisions)
    assert "最多处理 50 页" in decisions
    assert "最多处理 30 页" not in decisions  # 默认值不许留在任何一行里
    assert task.confirmation()["访问范围"]["最多页面"] == 50


def test_output_formats_narrow_to_what_the_request_asks_for() -> None:
    """旧实现恒为 xlsx+csv+jsonl 三种，「输出 CSV」不被采纳。"""
    assert compile_natural_language(
        "抓取 https://example.com/a 的标题、价格，输出 CSV"
    ).task.output_formats == ("csv",)
    # 没说格式时不收窄（保守：不替用户砍掉既有默认）
    assert compile_natural_language("保存 https://example.com/x").task.output_formats == (
        "xlsx",
        "csv",
        "jsonl",
    )


# ── 2. 不静默：unsupported 与 warnings 的不变量 ──────────────────────────


@pytest.mark.parametrize(
    "demand",
    [
        # ★ 排序 / 聚合统计**不在**这里：R5.1 起它们已有对等能力（见第 3 节），
        #   再列进"做不到"就是假话。
        "抓取 https://example.com/a 的标题、价格，做一个分组统计表",
        "抓取 https://example.com/a 的标题、价格，点击年份 2015 等待 AJAX",
        "抓取 https://example.com/a 的标题，在搜索框填写关键词后点击搜索",
        "抓取 https://example.com/a 的标题，抓取 iframe 里的表格",
    ],
)
def test_every_unsupported_item_has_a_stated_way_out(demand: str) -> None:
    """不变量：凡进 ``unsupported`` 的，必须能在 ``warnings`` 里找到去处。"""
    task = compile_natural_language(demand).task
    assert task.unsupported, "这条需求本应有做不到的部分，用例选错了"
    text = "".join(task.warnings)
    missing = [name for name in task.unsupported if name not in text]
    assert not missing, f"这些项进了 unsupported 却没进 warnings：{missing}"
    assert task.confirmation()["当前不支持"] == list(task.unsupported)


def test_nothing_unsupported_means_no_warning_noise() -> None:
    """反向：没有做不到的部分时不许凭空产生提醒（否则提醒会被当噪声忽略）。"""
    task = compile_natural_language("抓取 https://example.com/a 的标题、价格").task
    assert task.unsupported == ()
    assert task.warnings == ()


def test_every_rule_name_is_either_routed_or_declared_unsupported() -> None:
    """每条规则名都必须**恰好**落进"有对等能力"或"如实说做不到"之一。

    两边都不进 ⇒ 需求被静默丢掉；两边都进 ⇒ 对同一件事既说能做又说做不到。
    这是 R5.1 把排序/分组/聚合从 `unsupported` 移出后新立的完整性判据。
    """
    names = [name for name, _ in nlt._POST_PROCESSING_RULES]
    names += [name for name, _ in nlt._INTERACTION_RULES]
    assert names, "规则表是空的，下面的断言会变成空集对空集的假通过"
    unregistered = [
        name
        for name in names
        if name not in nlt._POST_PROCESSING_ROUTES and name not in nlt._DEMAND_GAP_GROUPS
    ]
    assert not unregistered, f"这些规则既没写去处也没进「做不到」清单：{unregistered}"
    both = sorted(set(nlt._POST_PROCESSING_ROUTES) & set(nlt._DEMAND_GAP_GROUPS))
    assert not both, f"这些项同时被声明为「能做」与「做不到」：{both}"
    unknown = sorted(set(nlt._DEMAND_GAP_GROUPS.values()) - set(nlt._DEMAND_GAP_ADVICE))
    assert not unknown, f"这些分组没有引导语：{unknown}"


# ── 3. 不撒谎：已经能做的事不得被写成「做不到」 ─────────────────────────


def test_post_processing_with_a_route_is_never_denied() -> None:
    """★ R5.1 起排序/分组/聚合统计已有对等能力（`transform --sort/--group-by/--agg`）：
    它们**不得**进 unsupported（那是假话），但必须给出**可执行**的去处。"""
    for demand in (
        "抓取 https://example.com/a 的标题、价格，按价格从低到高排序",
        "统计 https://example.com/forum 每个用户的帖子数",
        "抓取 https://example.com/a 的标题、价格，按分类分组",
    ):
        task = compile_natural_language(demand).task
        assert task.post_processing, f"用例选错了，这条需求没有读出后处理：{demand}"
        overlap = set(task.post_processing) & set(task.unsupported)
        assert not overlap, f"既有能力被说成了做不到：{overlap}"
        advice = "".join(task.warnings)
        assert "transform" in advice, f"只说能做、没给命令：{advice}"
        for name in task.post_processing:
            assert name in advice, f"后处理 {name} 没出现在提醒里"


def test_crosstab_is_still_declared_unsupported() -> None:
    """反向：R5.1 **没有**实现透视表 ⇒ 不能把它说成能做到（同一枚硬币的另一面）。"""
    task = compile_natural_language(
        "抓取 https://example.com/a 的标题、价格，做一个分组统计表"
    ).task
    assert "交叉表" in task.post_processing
    assert "交叉表" in task.unsupported
    assert any("交叉表" in warning for warning in task.warnings)


def test_auto_scroll_is_not_reported_as_unsupported() -> None:
    """R3.2 起自动路径已能生成 `scroll_bottom` 动作（实测 10 → 100 条）。

    把「滚动」列进 unsupported 会是**假话** —— 用户会以为要自己动手。
    """
    task = compile_natural_language(
        "滚动到底部加载全部引文，抓取 https://quotes.toscrape.com/scroll 的文本，输出 CSV"
    ).task
    assert not any("滚动" in item for item in task.unsupported)
    assert not any("滚动" in warning for warning in task.warnings)


def test_run_comparison_points_at_compare_runs_instead_of_denying_it() -> None:
    """「对比两次结果」有既有命令 `compare-runs` ⇒ 提醒要给命令，不能说"没有对等能力"。"""
    task = compile_natural_language("对比 https://example.com/a 的新旧价格差异").task
    assert "新旧对比" in task.post_processing
    assert "新旧对比" in task.unsupported  # 它确实不会**自动**产出
    advice = "".join(task.warnings)
    assert "compare-runs" in advice, "没有给出可执行的去处"
    assert "没有对等能力" not in advice, "把既有能力说成了没有"


# ── 4. 宁可少取：歧义 token 不许被当成字段名 ───────────────────────────


@pytest.mark.parametrize(
    "demand",
    [
        "抓取 该标签下所有引文",  # 半句话不是字段名
        "抓取 https://example.com/a 的标题、去除货币符号",  # 处理描述不是字段名
        "抓取 https://example.com/a 的标题",  # 单项不成列举
    ],
)
def test_ambiguous_tokens_are_not_taken_as_field_names(demand: str) -> None:
    """把半句话当字段名，比少一个字段更糟（项目既有的「歧义不猜」）。"""
    assert compile_natural_language(demand).task.fields == ()


# ── 5. 两条路径必须一致（否则「开 AI 反而更差」） ────────────────────────


def test_ai_path_carries_the_same_request_semantics() -> None:
    """AI 路径与确定性路径共用同一处接线 —— 同一条需求不该因"开不开 AI"而丢语义。

    旧实现两条路径都静默丢弃；只接一处则会变成开了 AI 反而更差，那比不接更坏。
    """
    request = "抓取 https://books.toscrape.com 的全部 50 页书籍的标题、价格，按价格从低到高排序，输出 CSV"
    local = compile_natural_language(request).task
    enhanced = compile_with_ai(request, _FakeProvider(_AI_JSON)).task
    assert enhanced.fields == local.fields
    assert enhanced.post_processing == local.post_processing
    assert enhanced.unsupported == local.unsupported
    assert enhanced.output_formats == local.output_formats
    assert enhanced.max_pages == local.max_pages
    assert enhanced.fields, "用例本身没有读到字段，上面的相等断言会变成空对空"
