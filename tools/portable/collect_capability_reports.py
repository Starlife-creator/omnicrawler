"""从已解压的产物目录里取能力报告，供等价性对账使用。

## 为什么单独成脚本

CI 里用 heredoc 内联 Python 有两类问题：YAML 缩进与 shell 引号规则容易打架，
出错时报文指向 YAML 而不是真正的逻辑。抽成脚本后：

* CI 只写一行命令；
* 逻辑可以本地单测（本文件就是被 `test_standard_equivalence.py` 覆盖的对象之一）。

## 用法

```
python tools/portable/collect_capability_reports.py <kept_dir> <output_dir>
```

从 `<kept_dir>` 下按 `EDITION.txt` 找出 Standard 与 Full 两个 release root，
各跑一次 `capabilities --mode quick`，把 JSON 写到 `<output_dir>/cap-{full,standard}.json`。

**找不到某个 edition 时输出 `::warning::` 并正常退出**——对账步骤会因此
报告"无法对账"而不是把它当成"等价"。这一点很重要：静默通过比失败更危险。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.child_env import utf8_env

#: 产物根目录下的可执行文件候选名（三平台不同）。
_EXE_CANDIDATES = ("omnicrawler-cli.exe", "omnicrawler-cli", "omnicrawler")


def find_release_roots(kept_dir: Path) -> dict[str, Path]:
    """按 `EDITION.txt` 找出各 edition 的 release root。"""
    roots: dict[str, Path] = {}
    for marker in sorted(kept_dir.rglob("EDITION.txt")):
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not first_line:
            continue
        # EDITION.txt 可能写成 "Full" 或 "Edition: Full"
        edition = first_line.split(":")[-1].strip().casefold()
        for known in ("standard", "full"):
            if edition == known:
                roots[known] = marker.parent
    return roots


def locate_executable(root: Path) -> Path | None:
    for name in _EXE_CANDIDATES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def probe(executable: Path, root: Path, *, timeout: float = 180.0) -> dict:
    """跑一次 `capabilities --mode quick`，返回解析后的 JSON（失败返回空 dict）。"""
    try:
        completed = subprocess.run(  # noqa: S603 - 命令由本模块构造
            [str(executable), "capabilities", "--mode", "quick"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            # 必须给子进程 UTF-8 环境：父进程按 UTF-8 解码输出，而 Windows
            # 默认控制台下 Python 子进程按 cp936 写，中文会变成 U+FFFD。
            env=utf8_env(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    stdout = completed.stdout or ""
    start = stdout.find("{")
    if start < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print("用法: collect_capability_reports.py <kept_dir> <output_dir>")
        return 2

    kept = Path(args[0])
    output = Path(args[1])
    if not kept.is_dir():
        print(f"::warning::解压目录不存在：{kept}，跳过")
        return 0
    output.mkdir(parents=True, exist_ok=True)

    roots = find_release_roots(kept)
    if not roots:
        print(f"::warning::{kept} 下未找到 EDITION.txt，跳过")
        return 0

    for edition in ("full", "standard"):
        root = roots.get(edition)
        if root is None:
            print(f"::warning::缺少 {edition} edition 产物，无法对账")
            return 0
        executable = locate_executable(root)
        if executable is None:
            print(f"::warning::{edition} 产物里没找到可执行文件，无法对账")
            return 0
        report = probe(executable, root)
        target = output / f"cap-{edition}.json"
        target.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(f"已写 {target}（{len(report)} 个顶层键）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
