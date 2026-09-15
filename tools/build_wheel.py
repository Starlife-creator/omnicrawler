"""构建 wheel 的**唯一入口**（W6.7-⑥）：先清 `build/`，再调 `pip wheel`。

## 为什么需要它

`pip wheel .` 在同一工作树里**第二次**会失败（§5.7 登记）：

```
[WinError 183] 当文件已存在时，无法创建该文件
```

setuptools 复用 `build/`，里面已有上次的 `.dist-info`。`rm -rf build` 后即正常；
CI 每次干净检出故不受影响 —— 受影响的是**人**（按文档照做第二次就炸）。

所以把这件事收进一个脚本：**清理 + 构建一体**，文档与 CI 都指向它，不再让人手抄命令
（也就不需要谁"记得先删 build/"这种依赖记忆的步骤）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

#: 会被 setuptools 复用、因而必须清理的构建残留
_STALE_BUILD_DIRS = ("build",)


def clean_build_dirs(root: Path) -> list[str]:
    """删掉 `root` 下的构建残留目录，返回被删掉的路径（相对 `root`）。"""
    removed: list[str] = []
    for name in _STALE_BUILD_DIRS:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(name)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理构建残留后构建 wheel（可重复执行）")
    parser.add_argument("--root", default=".", type=Path, help="项目根（默认当前目录）")
    parser.add_argument("-w", "--out", default="dist", type=Path, help="wheel 输出目录（默认 dist）")
    # 透传参数里常带 `--xxx`（如 `--no-build-isolation`）：`nargs="*"` 与 `REMAINDER`
    # 都会在"未知选项出现在其它选项之后"时报错退出，改用 `parse_known_args` 收下来。
    args, extra = parser.parse_known_args(argv)
    root = args.root.expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        print(f"错误：{root} 下没有 pyproject.toml", file=sys.stderr)
        return 2
    removed = clean_build_dirs(root)
    if removed:
        print(f"已清理构建残留：{', '.join(removed)}")
    out_dir = args.out if args.out.is_absolute() else root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        ".",
        "--no-deps",
        "-w",
        str(out_dir),
        *extra,
    ]
    return subprocess.call(command, cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(main())
