"""批次 6-4e：非 dict 运行结果的终态 payload 只允许有一处字面量。

S2.5.47 要求「`ApplicationService.run()` 返回非 dict 也要落终态、不卡 running」。
进程内后端（`InProcessBackend`）与独立 worker 进程（`WorkerRuntime`）原本各写一遍
同一段字面量，形状漂移就会出现「一个后端说 succeeded、另一个还在 running」。
这里钉住两件事：形状本身，以及除真源外没人再手写它。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.runtime.result_state import succeeded_payload

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "omnicrawler"
_TRUTH_SOURCE = "runtime/result_state.py"


def test_succeeded_payload_shape_is_the_documented_one() -> None:
    payload = succeeded_payload("plain-value")

    assert payload["status"] == "succeeded"
    assert payload["result"] == {"status": "succeeded", "value": "plain-value"}


def test_succeeded_payload_wraps_any_value_without_inspection() -> None:
    for value in (42, ["a"], {"k": 1}, None):
        payload = succeeded_payload(value)

        assert payload["result"]["value"] is value


def test_only_the_truth_source_spells_out_the_non_dict_payload() -> None:
    offenders: list[str] = []
    scanned = 0
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        relative = path.relative_to(_SRC_ROOT).as_posix()
        if relative == _TRUTH_SOURCE:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        if '"result": {"status": "succeeded", "value"' in text:
            offenders.append(relative)

    assert scanned > 100, "empty or truncated selection is not a pass"
    assert offenders == []


def test_both_backends_go_through_the_shared_helper() -> None:
    backend_source = (_SRC_ROOT / "runtime" / "execution_backend.py").read_text(encoding="utf-8")
    worker_source = (_SRC_ROOT / "runtime" / "worker_main.py").read_text(encoding="utf-8")

    assert "succeeded_payload(result)" in backend_source
    assert "succeeded_payload(result)" in worker_source


def test_reverse_assertion_an_installed_literal_turns_the_guard_red() -> None:
    polluted = (
        "def f(result):\n"
        '    return {"status": "succeeded", "result": {"status": "succeeded", "value": result}}\n'
    )

    assert '"result": {"status": "succeeded", "value"' in polluted


def test_clear_session_helper_is_the_only_place_that_forgets_the_session() -> None:
    source = (_SRC_ROOT / "runtime" / "execution_backend.py").read_text(encoding="utf-8")

    assert source.count("def _clear_session(self) -> None:") == 1
    # 三个字段成组清空（不带类型注解的赋值形态）：只允许在 _clear_session 里出现一次。
    # __init__ 里的初始化带注解（`self.session: WorkerSession | None = None`），不算。
    assert source.count("        self.session = None\n        self.session_file = None\n") == 1
    # 两个「忘掉会话」的调用点都必须走同一个 helper。
    assert source.count("self._clear_session()") == 2
