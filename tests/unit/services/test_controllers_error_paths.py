"""`services/controllers.py` 的错误包装层：5 个控制器共 12 条错误分支。

为什么要有这个文件：该模块覆盖率 29%，未覆盖的全是**错误分支**。
它是 GUI 与 CLI 共用的边界层 —— 把 service 抛的异常转成 `{"error", "status"}`，
所以「哪种异常给出哪句中文提示」是用户实际读到的唯一东西。

错误路径恰恰最容易**长期腐化而无人发现**：正常路径天天跑，错误路径只有出事才走，
而那时用户已经看不懂提示了。
"""

from __future__ import annotations

from typing import Any

import pytest

from omnicrawler.services.controllers import (
    ResultController,
    RunController,
    TaskController,
    TemplateController,
    _error,
)


class _Service:
    """按需抛异常的最小桩：只实现被调到的那些方法。"""

    def __init__(self, **behaviour: Any) -> None:
        self._behaviour = behaviour

    def __getattr__(self, name: str) -> Any:
        value = self._behaviour[name]
        if isinstance(value, BaseException):
            raise value
        return lambda *a, **k: value


def test_error_helper_shape() -> None:
    """`_error` 是唯一构造点，形状必须稳定（调用方按这两个键判断）。"""
    assert _error("x") == {"error": "x", "status": "error"}
    assert _error("x", status="warn") == {"error": "x", "status": "warn"}


# ── TaskController ────────────────────────────────────────────────


def test_task_load_reports_missing_config() -> None:
    """配置不存在要给「配置文件不存在」，不是「加载失败」—— 后者无法指导用户。"""
    out = TaskController(_Service(load=FileNotFoundError("a.yaml"))).load()
    assert "配置文件不存在" in out["error"]
    assert out["status"] == "error"


def test_task_load_wraps_other_errors() -> None:
    out = TaskController(_Service(load=RuntimeError("boom"))).load()
    assert "加载失败" in out["error"]


def test_task_compile_rejects_non_list_capabilities() -> None:
    """参数校验要在调用 service **之前**做 —— 否则错误来自 service 内部，难定位。"""
    out = TaskController(_Service(compile={})).compile("not-a-list")  # type: ignore[arg-type]
    assert "capabilities" in out["error"]


def test_task_compile_reports_unknown_capability() -> None:
    out = TaskController(_Service(compile=KeyError("ocr"))).compile(["ocr"])
    assert "未知的能力标识" in out["error"]


def test_task_compare_rejects_empty_path() -> None:
    out = TaskController(_Service(compare={})).compare("")
    assert "不能为空" in out["error"]


# ── RunController ─────────────────────────────────────────────────


@pytest.mark.parametrize("pages", [0, -1, 1.5, "3", None])
def test_run_sample_rejects_invalid_pages(pages: Any) -> None:
    """`pages` 必须是正整数 —— 注意 `True` 也是 int，但这里只校验正负与类型。"""
    out = RunController(_Service(sample={})).sample(pages)
    assert "正整数" in out["error"]


def test_run_reports_missing_config_distinctly() -> None:
    out = RunController(_Service(run=FileNotFoundError("x.yaml"))).run()
    assert "配置文件不存在" in out["error"]


def test_run_reports_permission_denied_distinctly() -> None:
    """权限不足与「运行失败」必须分开 —— 前者要用户改权限，后者要查日志。"""
    out = RunController(_Service(run=PermissionError("denied"))).run()
    assert "权限不足" in out["error"]


@pytest.mark.parametrize(
    ("method", "keyword"),
    [("pause", "暂停失败"), ("resume", "恢复运行失败"), ("stop", "停止失败")],
)
def test_run_control_failures_have_their_own_wording(method: str, keyword: str) -> None:
    """三个控制动作的提示不能混用 —— 用户要知道是哪个动作失败。"""
    controller = RunController(_Service(**{method: RuntimeError("x")}))
    out = getattr(controller, method)()
    assert keyword in out["error"]


# ── TemplateController ────────────────────────────────────────────


def test_template_search_rejects_non_string_query() -> None:
    """返回 **list** 的方法，错误要用「单元素错误 list」表示（调用方按 list 处理）。"""
    out = TemplateController().search(123)  # type: ignore[arg-type]
    assert isinstance(out, list) and len(out) == 1
    assert "必须是字符串" in out[0]["error"]


def test_template_search_empty_result_is_not_an_error() -> None:
    """空结果返回 `[]`，**不能**塞错误项 —— 否则调用方无法区分「没匹配」与「出错」。"""
    assert TemplateController().search("zzz-不存在-zzz") == []


def test_template_recommend_rejects_empty_url() -> None:
    out = TemplateController().recommend("")
    assert isinstance(out, list) and out and "不能为空" in out[0]["error"]


@pytest.mark.parametrize(
    ("ir", "template_id", "values", "keyword"),
    [
        ({}, "t", {}, "IR 映射不能为空"),
        ({"a": 1}, "", {}, "template_id"),
        ({"a": 1}, "t", "not-a-mapping", "values 必须是映射类型"),
    ],
)
def test_template_apply_validates_each_argument(
    ir: dict[str, Any], template_id: str, values: Any, keyword: str
) -> None:
    """三个参数各自有独立校验 —— 一次只报第一个错的那个。"""
    out = TemplateController().apply(ir, template_id, values)
    assert keyword in out["error"]


def test_template_apply_reports_missing_template() -> None:
    out = TemplateController().apply({"a": 1}, "no-such-template-id", {})
    assert "模板不存在" in out["error"]


# ── ResultController ──────────────────────────────────────────────


def test_result_query_reports_missing_results() -> None:
    out = ResultController(_Service(query=FileNotFoundError("runs"))).query()
    assert "未找到运行结果" in out["error"]


def test_result_export_rejects_non_string_run_id() -> None:
    out = ResultController(_Service(export={})).export(123)  # type: ignore[arg-type]
    assert "run_id" in out["error"]


def test_result_export_reports_permission_denied_distinctly() -> None:
    out = ResultController(_Service(export=PermissionError("denied"))).export("r1")
    assert "导出权限不足" in out["error"]


def test_result_export_reports_missing_target_distinctly() -> None:
    out = ResultController(_Service(export=FileNotFoundError("nope"))).export("r1")
    assert "导出目标不存在" in out["error"]


# ── 成功路径（错误分支已覆盖，这里补齐正常返回，避免「只会报错」的假象）──


@pytest.mark.parametrize(
    ("method", "kwargs", "payload"),
    [
        ("load", {}, {"config": "ok"}),
        ("validate", {}, {"ok": True}),
        ("compile", {"capabilities": ["ocr"]}, {"ir": 1}),
    ],
)
def test_task_controller_success_passthrough(
    method: str, kwargs: dict[str, Any], payload: dict[str, Any]
) -> None:
    """成功时原样透传 service 的返回值，不额外包一层。"""
    controller = TaskController(_Service(**{method: payload}))
    assert getattr(controller, method)(**kwargs) == payload


@pytest.mark.parametrize(
    ("method", "kwargs", "payload"),
    [
        ("sample", {"pages": 3}, {"pages": 3}),
        ("run", {}, {"status": "done"}),
        ("pause", {}, {"status": "paused"}),
        ("resume", {}, {"status": "running"}),
        ("stop", {}, {"status": "stopped"}),
    ],
)
def test_run_controller_success_passthrough(
    method: str, kwargs: dict[str, Any], payload: dict[str, Any]
) -> None:
    """五个动作的成功路径都必须透传。"""
    controller = RunController(_Service(**{method: payload}))
    assert getattr(controller, method)(**kwargs) == payload


def test_result_controller_success_passthrough() -> None:
    service = _Service(query={"rows": 1}, export={"path": "x.jsonl"})
    results = ResultController(service)
    assert results.query() == {"rows": 1}
    assert results.export("r1") == {"path": "x.jsonl"}


def test_run_sample_passes_pages_through() -> None:
    """参数校验不能吃掉合法值。"""
    assert RunController(_Service(sample={"n": 5})).sample(5) == {"n": 5}


def test_task_compare_requires_existing_target(tmp_path) -> None:
    """`compare` 在调 service **之前**先检查路径存在 —— 值得单独钉住。

    实测（2026-11-24）：这条前置校验此前未被覆盖。它比「让 service 抛
    FileNotFoundError」更好，因为能在读了半个文件之前就拒绝。
    """
    missing = TaskController(_Service(compare={})).compare(tmp_path / "nope.yaml")
    assert "比较目标不存在" in missing["error"]

    target = tmp_path / "b.yaml"
    target.write_text("project: {name: b}\n", encoding="utf-8")
    ok = TaskController(_Service(diff={"diff": []})).compare(target)
    assert ok == {"diff": []}
