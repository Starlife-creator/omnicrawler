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

from tools.child_env import utf8_env  # noqa: E402
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
    incremental: bool = False
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


def _child_env() -> dict[str, str]:
    """子进程环境：在父环境上**叠加** UTF-8 设置，供门禁的 stdout/stderr 走 UTF-8。

    实现已收进 :func:`tools.child_env.utf8_env`（同一类缺陷在别处也有，见该模块
    的说明）；这里保留一个薄封装，让调用点读起来仍是「本文件的子进程环境」。
    """
    return utf8_env()


def _run_gate(gate: Gate) -> GateOutcome:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, *gate.args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_child_env(),
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


def _changed_files(since: str = "HEAD~1") -> list[str]:
    """收集相对 ``since`` 及当前工作区的变更路径（用于 ``--incremental`` 本地快速反馈）。"""
    paths: set[str] = set()
    diff_out = _git("diff", "--name-only", since)
    if diff_out:
        paths.update(line.strip() for line in diff_out.splitlines() if line.strip())
    status_out = _git("status", "--porcelain")
    for raw in status_out.splitlines():
        if len(raw) > 3:
            entry = raw[3:].strip()
            if " -> " in entry:
                entry = entry.split(" -> ", 1)[1].strip()
            paths.add(entry.strip('"'))
    return sorted(p.replace("\\", "/") for p in paths if p)


def select_incremental_gates(
    selected: tuple[Gate, ...],
    changed: list[str],
) -> tuple[Gate, ...]:
    """按变更文件域收窄本地增量门禁，同时对 ``ruff`` / ``compileall`` 精准缩圈至变更文件。"""
    always = {"check_line_endings_policy", "check_gitignore_hygiene"}
    wanted = set(always)
    py_files = [p for p in changed if p.endswith(".py") and (REPO_ROOT / p).is_file()]
    has_py = any(p.endswith(".py") for p in changed)
    has_src = any(p.startswith("src/") for p in changed)
    has_gui = any("gui" in p for p in changed)
    has_doc = any(p.endswith(".md") or p.startswith("docs/") for p in changed)
    has_cli = any("cli" in p or "commands" in p for p in changed)
    # **跨平台模拟只在相关改动时跑**：它要真跑测试（114 秒），
    # 而 `--set static` 的其余门禁合计才 38 秒 —— 无条件跑会让
    # 「改一行 → 知道对不对」从秒级回到分钟级。
    #
    # 判据：改动落在「按平台/架构分支」的代码或它们的测试上。
    has_platform_surface = any(
        p.startswith(("src/omnicrawler/services/component_", "src/omnicrawler/core/capability"))
        or p.startswith("tests/unit/services/test_component")
        or p.startswith("tests/unit/browser/")
        or p in {"src/omnicrawler/services/browser_engines.py",
                 "src/omnicrawler/services/browser_kernels.py"}
        for p in changed
    )
    has_pkg = any(
        p in {"pyproject.toml", "uv.lock"}
        or p.startswith(("constraints/", "packaging/", ".github/"))
        or p.endswith((".sh", ".ps1", ".bat", ".command"))
        for p in changed
    )

    if has_py:
        wanted |= {
            "compileall",
            "ruff",
            "check_coding_standards",
            "check_architecture",
            "check_silent_exception_handlers",
            "check_subprocess_encoding",
        }
    if has_src:
        wanted |= {"mypy", "check_sdk_api", "check_network_boundaries", "check_minimal_install"}
    if has_platform_surface:
        wanted |= {"platform_matrix", "check_test_platform_defaults"}
    if has_gui:
        wanted |= {
            "check_gui_conventions",
            "check_gui_state_threading",
            "check_gui_test_layout",
            "check_main_window_size",
        }
    if has_doc or has_cli:
        wanted |= {
            "check_docs_consistency",
            "check_doc_numbers",
            "check_cli_docs",
            "check_agent_surface",
            "post_bump_steps",
            "check_anchor_integrity",
        }
    if has_pkg:
        wanted |= {
            "check_lockfile_consistency",
            "check_dependency_consistency",
            "check_build_profiles",
            "check_extras_expansion",
            "check_shell_scripts",
            "check_release_integrity",
        }

    narrowed: list[Gate] = []
    for gate in selected:
        if gate.name not in wanted and "static" in gate.sets:
            continue
        if gate.name == "ruff" and py_files:
            narrowed.append(
                Gate(
                    name=gate.name,
                    args=("-m", "ruff", "check", *py_files),
                    sets=gate.sets,
                    description=gate.description,
                    ci_note=gate.ci_note,
                )
            )
        elif gate.name == "compileall" and py_files:
            narrowed.append(
                Gate(
                    name=gate.name,
                    args=("-m", "py_compile", *py_files),
                    sets=gate.sets,
                    description=gate.description,
                )
            )
        else:
            narrowed.append(gate)
    return tuple(narrowed)


def verify(
    sets: list[str],
    *,
    fail_fast: bool = False,
    incremental: bool = False,
    since: str = "HEAD~1",
    changed_override: list[str] | None = None,
) -> EvidenceReport:
    selected = gates_for(sets)
    if incremental:
        changed = _changed_files(since) if changed_override is None else changed_override
        selected = select_incremental_gates(selected, changed)
    report = EvidenceReport(
        ok=True,
        sets=list(sets),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        duration_seconds=0.0,
        head=_git("rev-parse", "--short", "HEAD"),
        dirty=bool(_git("status", "--porcelain")),
        incremental=incremental,
    )

    mode_tag = f"，增量基线：{since}" if incremental else ""
    print(f"质量门禁自证（集合：{', '.join(sets)}{mode_tag}）")
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
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="仅针对相对 --since 及当前工作区变更涉及的域执行快速自证（全量交付仍须不带本旗标运行）",
    )
    parser.add_argument(
        "--since",
        default="HEAD~1",
        help="配合 --incremental 使用的 git 比较起点（默认 HEAD~1）",
    )
    args = parser.parse_args(argv)

    sets = list(GATE_SETS) if args.sets == "all" else [s.strip() for s in args.sets.split(",") if s.strip()]
    try:
        selected = gates_for(sets)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    if args.incremental:
        selected = select_incremental_gates(selected, _changed_files(args.since))

    if args.list:
        for gate in selected:
            marks = ",".join(sorted(gate.sets))
            print(f"{gate.name:<28} [{marks:<9}] {gate.description}")
        return 0

    report = verify(
        sets,
        fail_fast=args.fail_fast,
        incremental=args.incremental,
        since=args.since,
    )
    _print_summary(report)

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report.to_json(), encoding="utf-8")
        print(f"证据报告：{target}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
