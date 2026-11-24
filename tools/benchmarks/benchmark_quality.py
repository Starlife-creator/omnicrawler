"""任务质量基准入口：在本地固定任务上核对精确交付契约。

与 `tools/benchmarks/benchmark_quality.py` 的关系：吞吐基准（`omnicrawler benchmark`）回答「跑得多快」，
本工具回答「采到的数据对不对、全不全、有没有来源证据」——`优化方案.md` §1.2 采集能力的另一半。

```bash
python tools/benchmarks/benchmark_quality.py                 # 跑全部内置任务
python tools/benchmarks/benchmark_quality.py --task list-three-details --json report.json
```

全程**离线**：任务页面由本地 HTTP 服务提供，抽取规则与期望结果都在
`omnicrawler.services.quality_benchmark` 里以常量定义，因此「同一版本 → 同一任务 → 可比结果」。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from omnicrawler.services.quality_benchmark import TASKS, run_task  # noqa: E402
from tools.gate_registry import SKIP_EXIT_CODE  # noqa: E402

_COLUMNS = (
    ("task", 24),
    ("expected_records", 17),
    ("found_records", 14),
    ("completeness", 13),
    ("accuracy", 10),
    ("evidence_ratio", 15),
    ("mean_field_completeness", 24),
    ("unexpected", 12),
    ("duplicates", 11),
)


@dataclass(frozen=True, slots=True)
class _WorkdirProbe:
    """工作目录是否可建。``error`` 非空即「环境缺能力」，不是代码缺陷。"""

    target: Path
    error: str | None


def _probe_workdir(workdir: str | None) -> _WorkdirProbe:
    """第一次真正的失败发生在哪里，就在这里判定 —— 不做静态推断。

    为什么值得单独一层：判据只有「任务是否满足交付契约」这一条时才可复核。
    此前临时目录不可建会在任务中途抛 ``PermissionError``，与「抽取不准」
    混成同一个红，读报告的人无从区分是环境坏了还是代码坏了。
    """
    if workdir:
        target = Path(workdir)
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            return _WorkdirProbe(target, f"{type(exc).__name__}: {exc}")
        return _WorkdirProbe(target, None)

    temp_root = Path(tempfile.gettempdir())
    try:
        with tempfile.TemporaryDirectory(dir=temp_root):
            pass
    except OSError as exc:
        return _WorkdirProbe(temp_root, f"{type(exc).__name__}: {exc}")
    return _WorkdirProbe(temp_root, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="在本地固定任务上测量数据完整性与准确性")
    parser.add_argument("--task", action="append", help="只跑指定任务（可重复；默认全部）")
    parser.add_argument("--json", dest="json_path", help="把报告写入该 JSON 文件")
    parser.add_argument("--workdir", help="中间产物目录（默认系统临时目录）")
    args = parser.parse_args(argv)

    selected = [task for task in TASKS if not args.task or task.name in args.task]
    if not selected:
        print(f"没有匹配的任务；可用：{[task.name for task in TASKS]}", file=sys.stderr)
        return 2

    # 先确认「能不能跑」，再打印任何东西：临时目录不可建时 stdout 不该留下半张表。
    probe = _probe_workdir(args.workdir)
    if probe.error is not None:
        # 能力缺失 ⇒ 跳过码 + 末行原因，而不是把环境崩溃混成「代码判红」。
        # 判红应当只留给「任务不满足交付契约」这一件事。
        print(f"无法创建工作目录 {probe.target}：{probe.error}", file=sys.stderr)
        print(
            "跳过原因：临时目录不可写，质量基准需要可写的中间产物目录"
            f"（尝试过 {probe.target}；用 --workdir 指定一个可写目录）",
            file=sys.stderr,
        )
        return SKIP_EXIT_CODE

    header = "".join(name.ljust(width) for name, width in _COLUMNS)
    print(header)
    print("-" * len(header))

    scores = []
    with tempfile.TemporaryDirectory(dir=probe.target) as temp:
        base = Path(args.workdir) if args.workdir else Path(temp)
        for task in selected:
            score = run_task(task, workdir=base / task.name)
            scores.append(score)
            mapping = score.to_mapping()
            print(
                mapping["task"].ljust(24)
                + str(mapping["expected_records"]).ljust(17)
                + str(mapping["found_records"]).ljust(14)
                + f"{mapping['completeness']:.2f}".ljust(13)
                + f"{mapping['accuracy']:.2f}".ljust(10)
                + f"{mapping['evidence_ratio']:.2f}".ljust(15)
                + f"{mapping['mean_field_completeness']:.2f}".ljust(23)
                + str(mapping["unexpected_records"]).ljust(12)
                + str(mapping["duplicate_records"]).ljust(11)
            )

    failed = [score.task for score in scores if not score.ok]
    print()
    print(f"结论：{len(scores) - len(failed)}/{len(scores)} 个任务满足精确交付契约")
    print("说明：通过要求完整、准确、来源匹配、无额外/重复交付，且字段自报完整度可信。")

    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "tasks": [score.to_mapping() for score in scores],
                    "failed": failed,
                    "environment": dict(scores[0].environment) if scores else {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"报告：{target}")

    if failed:
        print(f"未满分任务：{failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
