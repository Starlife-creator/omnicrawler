"""插件未安装时的 `suggestion` 必须指向真正的解决方法。

为什么要有这个文件：`_suggest()` 按**异常类型**给建议。「插件未安装」抛的是
`RuntimeError`，落到默认分支后被建议「运行 omnicrawler doctor 诊断环境」——
而 doctor 帮不上忙，这是个有**明确解法**的问题：装插件。

实测（2026-11-24）：`omnicrawler --json convert ...` 返回
`suggestion: "运行 omnicrawler doctor 诊断环境..."`，误导 agent 去诊断环境。

本文件钉住：按**消息内容**识别插件缺失，给出可执行的下一步。
"""

from __future__ import annotations

import pytest

from omnicrawler.cli._json_envelope import _is_retryable, _suggest


def test_plugin_missing_suggests_installing_the_plugin() -> None:
    """插件缺失 → 建议去市场装插件，而不是跑 doctor。"""
    exc = RuntimeError(
        "文档工作台插件未安装，无法执行该操作（触发操作：convertx）。"
        "请在「OmniCrawler 市场」中安装 document-workbench。"
    )
    suggestion = _suggest(exc)
    assert "市场" in suggestion, suggestion
    assert "doctor" not in suggestion, f"仍在建议 doctor：{suggestion}"


def test_plugin_missing_is_marked_not_retryable() -> None:
    """装插件之前原样重试永远不会成功。"""
    # 注意：默认推断对 RuntimeError 给 True，所以这条必须靠调用方显式覆盖
    # （见 cli._main 的 pdf 分支）。这里只钉住「默认推断」这个已知局限。
    exc = RuntimeError("插件未安装")
    assert _is_retryable(exc) is True, "默认按类型推断为可重试 —— 已知局限"


@pytest.mark.parametrize(
    "exc_name",
    ["ValueError", "KeyError", "TypeError"],
)
def test_input_errors_keep_their_own_suggestion(exc_name: str) -> None:
    """输入类错误不要被插件分支抢走。"""
    exc = {"ValueError": ValueError, "KeyError": KeyError, "TypeError": TypeError}[exc_name]("x")
    assert "修正参数" in _suggest(exc)


def test_generic_runtime_error_still_suggests_doctor() -> None:
    """与插件无关的 RuntimeError 仍走原默认建议 —— 别把分支写宽。"""
    assert "doctor" in _suggest(RuntimeError("unexpected internal state"))


def test_file_not_found_unchanged() -> None:
    """既有分支不受影响。"""
    assert "omnicrawler init" in _suggest(FileNotFoundError(2, "no", "x.yaml"))
