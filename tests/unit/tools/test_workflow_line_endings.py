"""工作区行尾必须与 git 存储形态一致，且不得出现成片空行。

## 为什么需要这条守卫

本仓 `core.autocrlf=true`：git blob 里是 **LF**，检出到工作区是 **CRLF**。
用会做换行归一的文本工具编辑这类文件时，`\\r` 会被当成独立换行，
结果是**每行后多一个空行**——文件行数翻倍，而内容看着"还在"。

实测踩到两次：

* `release.yml` 292 行被写成 571 行（287 行是多余空行），
  diff 变成 550 行噪音，淹没了真实改动；
* 同一文件插入时还产生了重复的命令调用。

这类损坏**肉眼很难发现**（YAML 仍能解析、测试仍能通过），
只有看行数或 diff 规模才会暴露。

## 判据

1. `.github/workflows/*.yml` 归一后不得有成片空行
   （连续空行 ≥ 3 视为异常——正常的空行不会连成片）；
2. 归一后的行数必须与 git blob 的行数一致（防止"行尾被拆成两行"）；
3. 空行占比异常（> 30%）时判红。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO / ".github" / "workflows"

pytestmark = pytest.mark.skipif(not _WORKFLOWS.is_dir(), reason="需要 .github/workflows")


def _blob_lines(rel: str) -> list[str] | None:
    """读 git blob（LF 形态）的行。文件未跟踪时返回 None。"""
    try:
        out = subprocess.run(  # noqa: S603 - 固定命令
            ["git", "cat-file", "-p", f"HEAD:{rel}"],
            capture_output=True,
            cwd=str(_REPO),
            check=False,
        )
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.replace(b"\r\n", b"\n").decode("utf-8").splitlines()


def _workflow_files() -> list[Path]:
    return sorted(_WORKFLOWS.glob("*.yml"))


def test_workflows_have_no_split_lines() -> None:
    """工作区行数归一后必须与 blob 一致——行尾被拆成两行会在这里暴露。

    这是"用文本工具改 CRLF 文件"最典型的后果：292 行变 571 行。
    """
    checked = 0
    for path in _workflow_files():
        rel = path.relative_to(_REPO).as_posix()
        blob = _blob_lines(rel)
        if blob is None:
            continue
        work = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8").splitlines()
        checked += 1
        assert len(work) == len(blob), (
            f"{rel} 工作区 {len(work)} 行、git blob {len(blob)} 行 —— "
            f"差 {abs(len(work) - len(blob))} 行。"
            "典型原因：编辑时把 \\r 当成了独立换行，每行后多一个空行。"
        )

    assert checked > 0, "没有检查到任何 workflow —— 路径推算可能失效"


def test_workflows_have_no_long_blank_runs() -> None:
    """不得出现连续 3 个以上空行。

    正常 YAML 里空行只用来分隔块，不会连成片。
    """
    for path in _workflow_files():
        text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        run = 0
        worst = 0
        for line in text.splitlines():
            if line.strip():
                run = 0
            else:
                run += 1
                worst = max(worst, run)
        assert worst < 3, (
            f"{path.name} 有连续 {worst} 个空行 —— 疑似行尾被拆开导致"
        )


def test_workflows_have_reasonable_blank_ratio() -> None:
    """空行占比异常（> 30%）判红。

    正常 workflow 的空行占比通常在 10% 以内；成片空行会让它逼近 50%。
    """
    for path in _workflow_files():
        lines = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8").splitlines()
        if not lines:
            continue
        blanks = sum(1 for line in lines if not line.strip())
        ratio = blanks / len(lines)
        assert ratio < 0.3, (
            f"{path.name} 空行占比 {ratio:.0%}（{blanks}/{len(lines)}）—— "
            "疑似行尾损坏"
        )


def test_no_duplicate_step_names_within_same_job() -> None:
    """**同一个 job 内**不得有重名步骤 —— 插入时容易重复。

    实测踩到：插入 collect_code 保护时，收集命令被写了两遍。

    **不能按整个文件判重**：不同 job 可以有同名步骤（例如多个 job 都叫
    「Checkout ...」），那是合法的。判据必须以 job 为边界。
    """
    for path in _workflow_files():
        text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
        lines = text.splitlines()

        # 按顶层 job 切分（两个空格缩进且以 `name:` 结尾的行）
        job_starts: list[tuple[int, str]] = []
        for index, line in enumerate(lines):
            stripped = line.rstrip()
            if (
                stripped.startswith("  ")
                and not stripped.startswith("   ")
                and stripped.endswith(":")
                and not stripped.startswith("  #")
                and not stripped.startswith("  -")
            ):
                job_starts.append((index, stripped.strip().rstrip(":")))

        if not job_starts:
            continue

        for order, (start, job_name) in enumerate(job_starts):
            end = job_starts[order + 1][0] if order + 1 < len(job_starts) else len(lines)
            block = lines[start:end]
            names = [
                line.strip()[len("- name:"):].strip()
                for line in block
                if line.strip().startswith("- name:")
            ]
            duplicates = {n for n in names if names.count(n) > 1}
            assert not duplicates, (
                f"{path.name} 的 job {job_name!r} 内有重名步骤：{sorted(duplicates)}"
            )
