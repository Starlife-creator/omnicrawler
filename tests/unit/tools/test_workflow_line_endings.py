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

import re
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


def corruption_signature(raw: bytes) -> tuple[int, int]:
    """返回 `(孤立\\r 个数, \\r\\r\\n 个数)` —— 行尾被拆开的两个字节指纹。

    单独抽成函数是为了**能被正面验证**：只有「正常文件返回 (0,0)」而没有
    「损坏文件返回非零」的证据，等于没验证过判据（空集合恒真）。
    """
    return len(re.findall(rb"\r(?!\n)", raw)), raw.count(b"\r\r\n")


def test_corruption_signature_actually_detects_damage() -> None:
    """★ 反向验证判据本身：损坏的输入必须被认出来。

    只测「真实 workflow 干净」是不够的 —— 如果 `corruption_signature` 永远
    返回 (0, 0)，上面那条测试照样全绿，而它什么也没守住。
    """
    good = b"key: value\r\nnext: 1\r\n"
    assert corruption_signature(good) == (0, 0), "正常 CRLF 被判成损坏"

    # ① \r 被当成独立换行：\r\r\n
    doubled = b"key: value\r\r\nnext: 1\r\n"
    lone, dup = corruption_signature(doubled)
    assert dup == 1 and lone == 1, f"没认出 \\r\\r\\n：{(lone, dup)}"

    # ② 孤立 \r（后面没有 \n）：行被拆成两半
    lone_cr = b"key: value\rnext: 1\r\n"
    lone, dup = corruption_signature(lone_cr)
    assert lone == 1, f"没认出孤立 \\r：{(lone, dup)}"


def test_workflows_have_no_split_lines() -> None:
    """★ 直接检「行尾被拆开」的字节签名，不拿工作区去比 HEAD。

    这是"用文本工具改 CRLF 文件"最典型的后果：292 行变 571 行。

    **判据变更（2026-11-29）**：原实现比较「工作区归一后的行数」与「`HEAD` blob
    的行数」。那个判据只在文件**没被改过**时成立 —— 任何一次合法编辑（增加
    步骤、删掉注释）都会让它变红，于是它惩罚的是「正在改文件」而不是「文件坏了」。
    实测：本文件加了两步后 340 行 vs HEAD 269 行直接报错，而文件本身
    CRLF=340 / LF=340 / 孤立 CR=0 / `\\r\\r\\n`=0，**一个坏字节都没有**。

    真正的损坏签名是确定的，可以直接查：

    - **孤立 `\\r`**（后面不跟 `\\n`）：`\\r` 被当成换行符，拆出了半行；
    - **`\\r\\r\\n`**：`\\r` 先被当换行写了一次、原 `\\r\\n` 又写了一次。

    两者都是**编辑事故的指纹**，且与"文件这次改了多少行"无关。
    """
    checked = 0
    for path in _workflow_files():
        raw = path.read_bytes()
        checked += 1
        lone, doubled = corruption_signature(raw)
        assert doubled == 0, (
            f"{path.name} 含 {doubled} 处 `\\r\\r\\n` —— 行尾被拆开，"
            "每行后多一个空行"
        )
        assert lone == 0, (
            f"{path.name} 含 {lone} 个孤立 `\\r`（后面不跟 `\\n`）—— "
            "这是 `\\r` 被当成独立换行留下的指纹"
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
