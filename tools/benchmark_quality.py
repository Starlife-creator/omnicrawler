"""任务质量基准入口：在本地固定任务上测「完整、准确、有来源证据」。

与 `tools/benchmark_quality.py` 的关系：吞吐基准（`omnicrawler benchmark`）回答「跑得多快」，
本工具回答「采到的数据对不对、全不全、有没有来源证据」——`优化方案.md` §1.2 采集能力的另一半。

```bash
python tools/benchmark_quality.py                 # 跑全部内置任务
python tools/benchmark_quality.py --task list-three-details --json report.json
```

全程**离线**：任务页面由本地 HTTP 服务提供，抽取规则与期望结果都在
`omnicrawler.services.quality_benchmark` 里以常量定义，因此「同一版本 → 同一任务 → 可比结果」。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omnicrawler.services.quality_benchmark import TASKS, run_task  # noqa: E402

_COLUMNS = (
    ("task", 24),
    ("expected_records", 17),
    ("found_records", 14),
    ("completeness", 13),
    ("accuracy", 10),
    ("evidence_ratio", 15),
    ("mean_field_completeness", 23),
)


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

    header = "".join(name.ljust(width) for name, width in _COLUMNS)
    print(header)
    print("-" * len(header))

    scores = []
    with tempfile.TemporaryDirectory() as temp:
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
            )

    failed = [score.task for score in scores if not score.ok]
    print()
    print(f"结论：{len(scores) - len(failed)}/{len(scores)} 个任务满分（完整性与准确性均 1.0）")
    print("说明：'字段自报完整度' 是流水线自己在 evidence._quality 里报的口径，与外部比对互证。")

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
