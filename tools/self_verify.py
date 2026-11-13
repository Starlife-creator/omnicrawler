"""质量门禁自证入口：一条命令产出可引用的证据。

对应 `优化方案.md` §1.2 可维护性升级项——「AI 可端到端自主交付并**自证**」。
自证的前提是**门禁清单唯一**（:mod:`tools.gate_registry`）且**执行入口唯一**（本脚本）：

```bash
python tools/self_verify.py                 # static 集合（红线的核心，秒级）
python tools/self_verify.py --set static,tests
python tools/self_verify.py --set static --json evidence.json
python tools/self_verify.py --set static --fail-fast
```

报告里如实区分三种结果：**通过** / **失败** / **跳过**。
跳过不是「通过」——要么缺前置产物（`requires_file`），要么需要调用方提供参数（`needs_args`），
要么门禁**自己探测到所需能力缺失**并以 :data:`tools.gate_registry.SKIP_EXIT_CODE` 退出，
三者都会写明原因并计入 `skipped`，不会被静默吞掉。

第三类跳过的成因值得单独记一笔：Git for Windows 的 bash 在被禁命名管道的沙箱里连启动
都做不到（`couldn't create signal pipe, Win32 error 5`），`check_shell_scripts` 因此把
每个脚本都写成「语法错误」——**代码没坏，是环境缺能力**。门禁改用跳过码如实登记后，
这种假红不会再被误读成脚本缺陷。

证据结论是三态（`verdict`）：`passed` / `failed` / `unverified`。
**全部跳过或选中 0 条门禁时判 `unverified`（未验证）并返回非 0**——空集合不是通过，
否则「一条都没跑」会被当成「全部通过」混进证据链。

退出码：结论为 `passed` 返回 0；`failed` 或 `unverified` 返回 1。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python tools/self_verify.py` 直接执行
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.gate_registry import GATE_SETS, SKIP_EXIT_CODE, Gate, gates_for  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 失败时在报告里保留的输出尾部行数——足够定位，又不淹没汇总。
_TAIL_LINES = 20


@dataclass
class GateOutcome:
    name: str
    status: str  # passed / failed / skipped
    duration_seconds: float
    detail: str = ""


@dataclass
class EvidenceReport:
    ok: bool
    sets: list[str]
    started_at: str
    duration_seconds: float
    head: str
    dirty: bool
    gates: list[GateOutcome] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for g in self.gates if g.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for g in self.gates if g.status == "failed")

    @property
    def skipped(self) -> int:
        return sum(1 for g in self.gates if g.status == "skipped")

    @property
    def verdict(self) -> str:
        """三态结论：``passed`` / ``failed`` / ``unverified``。

        「跳过」不等于「通过」：一条门禁都没真正执行（选中 0 条，或全部因缺前置
        条件被跳过）时判 ``unverified``——证据链上是**未验证**，不是通过。
        """
        if self.failed:
            return "failed"
        if self.passed == 0:
            return "unverified"
        return "passed"

    def to_json(self) -> str:
        payload = asdict(self)
        payload["passed"] = self.passed
        payload["failed"] = self.failed
        payload["skipped"] = self.skipped
        payload["verdict"] = self.verdict
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _git(*args: str) -> str:
    """在仓库根执行只读 git 命令；任何失败都退化为空串（不因缺 git 而中断自证）。"""
    try:
        done = subprocess.run(
            ("git", *args), cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _run_gate(gate: Gate) -> GateOutcome:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, *gate.args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = round(time.monotonic() - started, 2)
    if done.returncode == 0:
        return GateOutcome(gate.name, "passed", elapsed)

    output = (done.stdout + done.stderr).strip()
    if done.returncode == SKIP_EXIT_CODE:
        # 门禁自述「环境缺能力」：不是代码判红。原因取输出的最后一行——约定是门禁把
        # 缺的能力写在末行；没写就显式记账，不假装有原因（跳过不允许无声）。
        reason = output.splitlines()[-1].strip() if output else ""
        return GateOutcome(
            gate.name,
            "skipped",
            elapsed,
            reason or "门禁以跳过码退出但未写明原因（应补一条可读理由）",
        )

    tail = "\n".join(output.splitlines()[-_TAIL_LINES:])
    return GateOutcome(gate.name, "failed", elapsed, f"退出码 {done.returncode}\n{tail}")


def _skip_reason(gate: Gate) -> str | None:
    """返回跳过原因；``None`` 表示可以执行。"""
    if gate.needs_args:
        names = "、".join(gate.needs_args)
        return f"需要调用方提供参数 {names}" + (f"（{gate.ci_note}）" if gate.ci_note else "")
    if gate.requires_file and not (REPO_ROOT / gate.requires_file).exists():
        return f"缺少前置路径 {gate.requires_file}"
    return None


def verify(sets: list[str], *, fail_fast: bool = False) -> EvidenceReport:
    selected = gates_for(sets)
    report = EvidenceReport(
        ok=True,
        sets=list(sets),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        duration_seconds=0.0,
        head=_git("rev-parse", "--short", "HEAD"),
        dirty=bool(_git("status", "--porcelain")),
    )

    print(f"质量门禁自证（集合：{', '.join(sets)}）")
    started = time.monotonic()
    for gate in selected:
        reason = _skip_reason(gate)
        if reason is not None:
            report.gates.append(GateOutcome(gate.name, "skipped", 0.0, reason))
            print(f"  [跳过] {gate.name:<28} {reason}")
            continue

        outcome = _run_gate(gate)
        report.gates.append(outcome)
        if outcome.status == "passed":
            print(f"  [通过] {gate.name:<28} {outcome.duration_seconds:.2f}s")
        elif outcome.status == "skipped":
            # 门禁自述的能力缺失：如实记为跳过并打印原因，不算失败（环境假红不再污染判据）。
            print(f"  [跳过] {gate.name:<28} {outcome.detail}")
        else:
            report.ok = False
            print(f"  [失败] {gate.name:<28} {outcome.duration_seconds:.2f}s")
            print("\n".join(f"        {line}" for line in outcome.detail.splitlines()))
            if fail_fast:
                break

    report.duration_seconds = round(time.monotonic() - started, 2)
    # 「跳过」不是「通过」：全部跳过（或选中 0 条）判未验证，证据链上不得记为通过。
    report.ok = report.verdict == "passed"
    return report


def _print_summary(report: EvidenceReport) -> None:
    print()
    print(
        f"汇总：{report.passed} 通过 / {report.failed} 失败 / {report.skipped} 跳过，"
        f"共 {report.duration_seconds}s"
    )
    print(f"证据：HEAD={report.head or '<未知>'}，工作区={'有未提交改动' if report.dirty else '干净'}")
    print(f"结论：{'通过' if report.verdict == 'passed' else ('未通过' if report.verdict == 'failed' else '未验证')}")
    if report.verdict == "unverified":
        print("说明：没有任何门禁真正执行（全部跳过）——跳过不等于通过，本报告不能作为交付证据。")


def main(argv: list[str] | None = None) -> int:
    # Windows 默认控制台是 GBK。门禁描述含 `↔`(U+2194) 等非 GBK 字符时，
    # `--list` 会抛 UnicodeEncodeError（同族的 check_*.py 已有 cp1252 回归测试，
    # 本入口此前漏了）。只放宽 errors 而不改 encoding——中文仍走 GBK 正常显示，
    # 仅少数非 GBK 字符退化为 `?`，不再崩溃。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(description="按门禁清单执行并产出证据报告")
    parser.add_argument(
        "--set",
        dest="sets",
        default="static",
        help=f"逗号分隔的门禁集合（{', '.join(GATE_SETS)}）或 all，默认 static",
    )
    parser.add_argument("--json", dest="json_path", help="把证据报告写入该 JSON 文件")
    parser.add_argument("--fail-fast", action="store_true", help="首个失败即停止")
    parser.add_argument("--list", action="store_true", help="只列出所选门禁，不执行")
    args = parser.parse_args(argv)

    sets = list(GATE_SETS) if args.sets == "all" else [s.strip() for s in args.sets.split(",") if s.strip()]
    try:
        selected = gates_for(sets)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    if args.list:
        for gate in selected:
            marks = ",".join(sorted(gate.sets))
            print(f"{gate.name:<28} [{marks:<9}] {gate.description}")
        return 0

    report = verify(sets, fail_fast=args.fail_fast)
    _print_summary(report)

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.to_json(), encoding="utf-8")
        print(f"证据报告：{target}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
