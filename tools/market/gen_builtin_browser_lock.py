"""生成内置 Chromium 的组件声明（§15.16 第 4 批 · 工具脚本，非运行时依赖）。

## 为什么需要这个脚本

内置 Chromium 有 **310 个文件 / 415 MiB**。手写清单不可能对，
而且一旦配方与真实字节漂移，就违反了「声明必须能从字节推导」。
所以清单**从字节生成**，并由门禁校验它与字节一致。

## 它做什么

1. 定位随包的 Chromium 目录（`runtime_paths.bundled_browser_executable()`）；
2. 逐文件算 sha256，写出 `files.lock.json`（相对安装根）；
3. 给出构建期需要的**平台/架构**探测结果，供人工确认配方字段。

## 它不做什么

**不复制、不移动任何字节。** 第 4 批的裁定是「字节不动，只加声明」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

#: 单个组件的文件清单文件名。与 `component.json` 的 `files` 同构，
#: 但这份放在**市场仓**里、随配方一起签名。
LOCK_NAME = "files.lock.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


#: 各平台 Chromium 可执行文件在 `browsers/chromium-*/` 下的相对路径。
#: 与 `runtime_paths.bundled_browser_executable()` 的 patterns 保持同源。
_EXECUTABLE_PATTERNS = (
    "chrome-win/chrome.exe",
    "chrome-win64/chrome.exe",
    "chrome-linux/chrome",
    "chrome-linux64/chrome",
    "chrome-mac/Chromium",
    "chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
)


def _find_browser_executable(browsers: Path) -> Path | None:
    """在产物布局里定位 Chromium 可执行文件。"""
    for chromium_dir in sorted(browsers.glob("chromium-*")):
        for pattern in _EXECUTABLE_PATTERNS:
            match = next(chromium_dir.glob(pattern), None)
            if match is not None and match.is_file():
                return match
    return None


def build_lock(browser_dir: Path, install_root: Path) -> dict:
    """把浏览器目录折成 `{相对路径: {sha256, size}}`。"""
    root = install_root.resolve()
    files: dict[str, dict[str, object]] = {}
    total = 0
    for path in sorted(browser_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.resolve().relative_to(root).as_posix()
        size = path.stat().st_size
        files[relative] = {"sha256": sha256_of(path), "size": size}
        total += size
    return {
        "schema": 1,
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成内置 Chromium 的文件哈希清单")
    parser.add_argument("--repo-root", required=True, help="宿主仓根（含 src/）")
    parser.add_argument("--out", required=True, help="输出目录（市场仓里的组件目录）")
    parser.add_argument("--check", action="store_true", help="只校验不写盘")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    # 两种运行形态：
    #
    # 1. **开发机**：`--repo-root` 是源码根（含 `src/`），
    #    走 `runtime_paths` 走同一条查找逻辑；
    # 2. **发布流程**：`--repo-root` 是**解压后的产物根**
    #    （`<temp>/Standard/OmniCrawler`），那里**没有 `src/`** ——
    #    冻结产物只有可执行文件与 `browsers/`。
    #
    # 早先只写死第 1 种，CI 里会 `sys.path.insert(.../src)` 然后 import 失败。
    # 所以这里**先判断形态**：有 `src/omnicrawler` 就 import，
    # 否则直接按产物布局定位（`<root>/browsers/`）。
    repo_root = Path(args.repo_root).resolve()
    source_tree = repo_root / "src" / "omnicrawler"

    if source_tree.is_dir():
        sys.path.insert(0, str(repo_root / "src"))
        from omnicrawler.core.runtime_paths import (  # noqa: PLC0415
            bundled_browser_executable,
            install_root,
        )

        executable = bundled_browser_executable()
        root = Path(install_root()).resolve()
    else:
        # 产物布局：浏览器在 `<root>/browsers/chromium-*/…`
        root = repo_root
        browsers = root / "browsers"
        if not browsers.is_dir():
            print(f"产物里没有 browsers/：{browsers}")
            return 1
        executable = _find_browser_executable(browsers)

    if executable is None:
        print("没找到随包的 Chromium —— 本机可能没解压产物，跳过")
        return 0

    # 组件覆盖整个内核目录（可执行文件所在的那一层往上到 chromium-<ver>/）
    browser_dir = executable.parent
    while browser_dir.name.startswith(("chrome-", "chrome_")) or browser_dir.name in {
        "chrome-win",
        "chrome-win64",
        "chrome-linux",
        "chrome-linux64",
        "chrome-mac",
    }:
        browser_dir = browser_dir.parent

    lock = build_lock(browser_dir, root)
    lock["browser_dir"] = browser_dir.name
    lock["platform"] = sys.platform
    lock["machine"] = platform.machine()
    lock["executable"] = executable.resolve().relative_to(root).as_posix()

    out_dir = Path(args.out)
    target = out_dir / LOCK_NAME
    payload = json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not target.is_file():
            print(f"[FAIL] 缺少 {LOCK_NAME}")
            return 1
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("files") != lock["files"]:
            print("[FAIL] 文件清单与真实字节不一致（配方漂移）")
            return 1
        print(f"[OK] 清单一致：{lock['file_count']} 个文件，{lock['total_bytes']} 字节")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    print(f"已写入 {target}")
    print(f"  浏览器目录：{browser_dir.name}")
    print(f"  文件数：{lock['file_count']}")
    print(f"  总字节：{lock['total_bytes']}（约 {lock['total_bytes'] // 1024 // 1024} MiB）")
    print(f"  可执行文件：{lock['executable']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
