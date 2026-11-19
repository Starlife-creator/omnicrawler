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


def _load_child_env():
    """按文件路径加载 `tools/child_env.py`。

    **不能用 `from tools.child_env import ...`**：本脚本在 CI 里是被
    `python tools/portable/collect_capability_reports.py` 直接执行的，
    此时 `sys.path[0]` 是 `tools/portable/`，仓库根不在路径上
    ⇒ `ModuleNotFoundError: No module named 'tools'`（真实 CI 踩到）。

    与同目录 `portable_smoke_test.py` 加载兄弟模块的做法保持一致。
    """
    import importlib.util as _ilu

    root = Path(__file__).resolve().parents[2]  # 仓库根
    spec = _ilu.spec_from_file_location("_child_env", root / "tools" / "child_env.py")
    if spec is None or spec.loader is None:  # pragma: no cover - 文件缺失才走到
        raise ImportError(f"无法加载 {root / 'tools' / 'child_env.py'}")
    module = _ilu.module_from_spec(spec)
    # 必须先注册：dataclass 求值类型注解时会查 sys.modules[cls.__module__]
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_utf8_env = _load_child_env().utf8_env

#: 产物根目录下的可执行文件候选名（三平台不同）。
_EXE_CANDIDATES = ("omnicrawler-cli.exe", "omnicrawler-cli", "omnicrawler")


def edition_from_marker(text: str) -> str:
    """从 `EDITION.txt` 的内容里认出 edition，返回 `standard` / `full` / 空串。

    **不能只做精确匹配**：三个平台的构建脚本实际写入的是整句

        OmniCrawler Full portable edition
        OmniCrawler Standard portable edition

    而不是裸的 `Full`。早先按 `text == "full"` 判断会永远失败，导致
    等价性对账在真实产物上**静默跳过**（实测核实构建脚本后才发现）。

    做法：先按 `:` 取尾（兼容 `Edition: Full` 这种写法），再按词匹配；
    取**最后**一个出现的 edition 词——句子里的第一个 `OmniCrawler` 不是 edition。
    """
    first_line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if not first_line:
        return ""
    # 兼容 "Edition: Full" 这类带前缀的写法
    candidate = first_line.split(":")[-1].strip().casefold()
    tokens = [token.strip(".,;()[]") for token in candidate.replace("-", " ").split()]
    for token in reversed(tokens):
        if token in ("standard", "full"):
            return token
    return ""


def find_release_roots(kept_dir: Path) -> dict[str, Path]:
    """按 `EDITION.txt` 找出各 edition 的 release root。"""
    roots: dict[str, Path] = {}
    for marker in sorted(kept_dir.rglob("EDITION.txt")):
        try:
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        edition = edition_from_marker(text)
        if edition:
            roots[edition] = marker.parent
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
            env=_utf8_env(),
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
            # 同样必须返回非 0：没有可执行文件就拿不到报告，对账无法进行。
            # 早先这里返回 0，CI 会继续拿缺一份报告去对账（实测踩到）。
            print(f"::error::{edition} 产物里没找到可执行文件，无法对账")
            return 3
        report = probe(executable, root)
        if not report:
            # **探测失败必须显式报错**，不能写个空 {} 就当"这个 edition 零能力"。
            # 否则对账会得出「Standard 缺 N 项能力」这种误导性结论——
            # 真实原因是"能力报告没读出来"，不是产品缺功能（实测踩到）。
            print(
                f"::error::{edition} 的能力报告读不出来"
                f"（executable={executable}），无法对账"
            )
            return 3
        target = output / f"cap-{edition}.json"
        target.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        print(f"已写 {target}（{len(report)} 个顶层键）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
