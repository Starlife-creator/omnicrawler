"""CI `continue-on-error` 豁免必须有到期日（批次 2「判据可信」）。

由来：本仓 4 处 `continue-on-error: true`（trivy 镜像扫描 / dependency-review / bandit SAST /
三平台 GUI offscreen 矩阵）全部标注「占位，待基线三审后改为阻断」。但「待…后」没有期限，
于是占位可以无限期存在——**门禁看起来有、实际从不阻断**。这与
`check_coverage_gates.py` 里 `BASELINE_PENDING_GATES` 曾经的问题是同一形态。

本契约把它变成会失败的检查：
* 每一处 `continue-on-error: true` 的**相邻注释必须写明到期日**（`豁免到期：YYYY-MM-DD`）；
* 到期日不得已过——过期即判红，逼着要么转阻断、要么显式续期并说明卡点；
* 反向断言：把「无到期日」的样本喂给解析器，必须被识别出来（保证不是恒真式）。
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# `continue-on-error: true` 出现的位置，其**上方注释区**里必须含到期日。
_EXPIRY = re.compile(r"豁免到期[：:]\s*(\d{4}-\d{2}-\d{2})")

# 向上回溯多少行找注释。到期说明可能写在该 step 的 name 上方（中间隔着
# `- name:` / `uses:` 等行），所以不能只收集「紧邻的连续注释」。
_LOOKBACK = 20


def _waiver_sites() -> list[tuple[str, int, str]]:
    """返回 (文件名, 行号, 上方注释区文本) —— 只取 `continue-on-error: true`。"""
    sites: list[tuple[str, int, str]] = []
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not re.match(r"\s*continue-on-error:\s*true\s*$", line):
                continue
            start = max(0, index - _LOOKBACK)
            block = [
                candidate.strip()
                for candidate in lines[start:index]
                if candidate.strip().startswith("#")
            ]
            sites.append((path.name, index + 1, "\n".join(block)))
    return sites


def _missing_expiry(sites: list[tuple[str, int, str]]) -> list[str]:
    """返回缺少到期日或到期日已过的位置（供测试与反向断言共用）。"""
    today = date.today()
    problems: list[str] = []
    for name, lineno, block in sites:
        match = _EXPIRY.search(block)
        if match is None:
            problems.append(f"{name}:{lineno} 缺少「豁免到期：YYYY-MM-DD」")
            continue
        expires = date.fromisoformat(match.group(1))
        if expires < today:
            problems.append(f"{name}:{lineno} 豁免已于 {expires} 过期，必须转阻断或显式续期")
    return problems


def test_every_continue_on_error_has_an_expiry_date() -> None:
    """每处 `continue-on-error: true` 的上方注释都必须写明到期日。"""
    sites = _waiver_sites()
    assert sites, "扫描不到任何 continue-on-error —— 解析失效（空集不能当通过）"
    assert not _missing_expiry(sites), (
        "以下 CI 豁免没有到期日或已过期（「占位」会因此变成永久不阻断）：\n"
        + "\n".join(_missing_expiry(sites))
    )


def test_waiver_count_matches_known_baseline() -> None:
    """豁免数量只降不升：新增 `continue-on-error` 必须同时更新本基线。

    这不是为了精确记账，而是防止「顺手加一个 continue-on-error 绕过红线」——
    新增必须走一次显式决定（改这里的数字，并在注释里写到到期日）。
    """
    expected = 4
    sites = _waiver_sites()
    assert len(sites) <= expected, (
        f"CI 豁免从 {expected} 处增加到 {len(sites)} 处："
        f"{[(name, line) for name, line, _ in sites]}。"
        "新增 continue-on-error 会静默削弱红线，请确认后同步更新本基线"
    )


def test_reverse_assertion_undated_waiver_is_detected() -> None:
    """**反向断言**：没有到期日的豁免样本必须被判为问题。"""
    sites = [("fake.yml", 1, "# 占位，待基线三审后改为阻断")]
    problems = _missing_expiry(sites)
    assert problems and "缺少" in problems[0]


def test_reverse_assertion_expired_waiver_is_detected() -> None:
    """**反向断言**：到期日已过的豁免必须被判为问题。"""
    sites = [("fake.yml", 1, "# 豁免到期：2020-01-01")]
    problems = _missing_expiry(sites)
    assert problems and "过期" in problems[0]


@pytest.mark.parametrize("expiry", ["2027-03-31"])
def test_known_expiry_date_is_in_the_future(expiry: str) -> None:
    """本批新增的到期日必须确实在未来，否则等于「加了等于没加」。"""
    assert date.fromisoformat(expiry) > date.today()
