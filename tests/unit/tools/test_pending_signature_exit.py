"""`tools/market/pending_signature.py --check` 的退出码语义。

为什么要有这个文件：该工具自身**零测试**，而它的退出码恰恰是「就绪」的定义所在。
实测（2026-11-24）：待签条目全部入库、`pending-signature/` 清空之后，`--check`
恒定返回 **1** —— 因为判据写的是 `total and ready == total`，`total == 0` 时
直接短路成假。于是「做完了」被报成「做坏了」，下一个会话会据此去翻一个
并不存在的阻塞项。

这与仓库既有的『空集合必须判红』纪律方向相反，但**不矛盾**：
- 门禁空集判红，防的是「判据失效却假装通过」；
- 这里的 0 是**真实结论**（没有待签条目），必须报成功，并说清楚为什么是 0。

本文件钉住三件事：

* `total == 0` ⇒ 0（成功），且输出说明「没有待签条目」；
* `total > 0` 且全部就绪 ⇒ 0；
* `total > 0` 且有未就绪 ⇒ 1。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TOOL_PATH = _REPO_ROOT / "tools" / "market" / "pending_signature.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_pending_signature_tool", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def market_repo(tmp_path: Path) -> Path:
    """最小市场仓：只要有 pending-signature/ 目录就够（check 只看它）。"""
    market = tmp_path / "market"
    (market / "pending-signature").mkdir(parents=True)
    return market


def _run(tool: ModuleType, market: Path, ready: int, total: int, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    tool.check_all = lambda *_a, **_k: (ready, total)  # noqa: ARG005 - 替身即可
    code = tool.main(["--market", str(market), "--check"])
    return code, capsys.readouterr().out


def test_empty_pending_directory_is_success(market_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """没有待签条目 ⇒ 成功，并说明原因。"""
    tool = _load_tool()
    code, out = _run(tool, market_repo, 0, 0, capsys)
    assert code == 0, f"0 个待签条目必须报成功，实际 {code}：{out}"
    assert "没有待签条目" in out
    assert "不是失败" in out, "必须说清楚 0 不等于出错，否则读的人仍会以为坏了"


def test_all_ready_is_success(market_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tool = _load_tool()
    code, out = _run(tool, market_repo, 3, 3, capsys)
    assert code == 0, out
    assert "3 / 3" in out


def test_partially_ready_is_failure(market_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """有未就绪条目必须判红 —— 否则「就绪」这个词失去意义。"""
    tool = _load_tool()
    code, out = _run(tool, market_repo, 2, 3, capsys)
    assert code == 1, out
    assert "2 / 3" in out


def test_missing_market_repository_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """市场仓不存在 ⇒ 退出码 2（区别于「就绪与否」的 0/1）。"""
    tool = _load_tool()
    code = tool.main(["--market", str(tmp_path / "absent"), "--check"])
    assert code == 2
    assert "市场仓不存在" in capsys.readouterr().out
