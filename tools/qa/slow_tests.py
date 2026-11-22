"""找出「比同一目录其它用例慢一个数量级」的测试。

## 这个工具解决什么

2026-11-22：CI 的 `coverage run -m pytest` 跑了 **90 分钟**仍未结束，
而本地全量只要 **326 秒**（带覆盖率插桩）。差距 16 倍。

排查时我逐个假设、逐个推翻：

| 假设 | 结果 |
|---|---|
| `env_markers` 探测慢 | ❌ 有 `_cache`，只探一次 |
| 某条测试单项慢 | ❌ 本地最慢 11.85 秒 |
| 新增测试导致 | ❌ 期间无新增网络测试 |
| 3.13 本身慢 | ❌ 本地同为 3.13.1 |
| 覆盖率插桩开销 | ❌ 本地带插桩仍只 326 秒 |
| 单条测试卡住 | ❌ `faulthandler_timeout` 实测 3 秒就能报栈，CI 90 分钟没报 |

**根因至今未定位**。但排查过程本身暴露了一个更基础的问题：
**我没有一个「一眼看出哪条慢」的工具**，只能靠反复跑全量 + 猜。

## 这个工具做什么

跑一遍全量，把耗时**按目录与按用例**两个维度汇总，标出：

* 单条超过阈值的用例（默认 1 秒）
* 单个目录总耗时占比最高的前几个

用途不是「优化测试」（那要看具体情况），而是**在排查时先有数据**，
不必从零猜起。

## 用法

```powershell
python tools/qa/slow_tests.py                 # 全量，阈值 1.0 秒
python tools/qa/slow_tests.py --threshold 0.5
python tools/qa/slow_tests.py --path tests/unit/services
```

## 边界

* **它是测量，不是判据**：不设「必须多快」的门槛 —— 机器差异太大，
  阈值化会变成噪音源。
* 跑一次要几分钟（等于跑全量）。它是**诊断时用**，不是每次提交都跑。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

OK_MARK = "[OK]"
BAD_MARK = "[FAIL]"

_ROOT = Path(__file__).resolve().parents[2]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: pytest `--durations` 的输出行，如 `2.04s call     tests/unit/x.py::test_y`。
_DURATION_LINE = re.compile(
    r"^(?P<seconds>\d+\.\d+)s\s+(?P<phase>\w+)\s+(?P<nodeid>\S+)"
)


def parse_durations(output: str) -> list[tuple[float, str, str]]:
    """从 pytest 输出里提取 `(耗时, 阶段, nodeid)`。"""
    found: list[tuple[float, str, str]] = []
    for line in output.splitlines():
        match = _DURATION_LINE.match(line.strip())
        if match:
            found.append(
                (
                    float(match.group("seconds")),
                    match.group("phase"),
                    match.group("nodeid"),
                )
            )
    return found


def by_directory(rows: list[tuple[float, str, str]]) -> dict[str, float]:
    """按测试文件所在目录汇总耗时。"""
    totals: dict[str, float] = defaultdict(float)
    for seconds, _phase, nodeid in rows:
        path = nodeid.split("::")[0]
        parent = str(Path(path).parent).replace("\\", "/")
        totals[parent] += seconds
    return dict(totals)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="列出比同目录其它用例慢一个数量级的测试",
    )
    parser.add_argument("--path", default="tests/unit", help="测试范围")
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="单条用例的告警阈值（秒，默认 1.0）",
    )
    parser.add_argument("--top", type=int, default=25, help="最多列出多少条")
    args = parser.parse_args(argv)

    from tools.child_env import utf8_env

    print(f"跑 {args.path} 并记录耗时（这会跑一遍全量）…")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            args.path,
            "-q",
            "-p",
            "no:cacheprovider",
            "--durations=200",
            "--durations-min=0.1",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
        check=False,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    rows = parse_durations(combined)

    # **防空集恒真**：一条耗时都没解析出来，说明格式变了。
    if not rows:
        print(f"{BAD_MARK} 没解析到任何耗时数据 —— pytest 输出格式可能已变")
        tail = "\n".join(combined.splitlines()[-8:])
        print(f"输出末尾：\n{tail}")
        return 1

    print()
    print("=== 最慢的用例 ===")
    rows.sort(reverse=True)
    slow = [r for r in rows if r[0] >= args.threshold]
    if slow:
        for seconds, phase, nodeid in slow[: args.top]:
            print(f"  {seconds:7.2f}s  {phase:8} {nodeid}")
    else:
        print(f"  （没有超过 {args.threshold} 秒的用例）")

    print()
    print("=== 按目录汇总 ===")
    dirs = by_directory(rows)
    for directory, total in sorted(dirs.items(), key=lambda kv: -kv[1])[:12]:
        count = sum(1 for r in rows if r[2].startswith(directory))
        print(f"  {total:8.2f}s  {count:4} 条  {directory}")

    print()
    total_all = sum(r[0] for r in rows)
    print(f"{OK_MARK} 共解析 {len(rows)} 条有耗时的用例，合计 {total_all:.1f} 秒")
    print(
        "提示：这只是**测量**。若某条远超其它，先看它是否真发网络请求或等锁；"
        "若整体都慢，那是环境问题（CI 与本地差异），不要逐条优化。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
